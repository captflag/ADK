"""Numbers Guard: every figure an agent states must come from a tool (ADR 0003).

The rule is in every agent's instructions, but instructions are only a
request. This plugin enforces it. During each run it records every figure the
user wrote and every figure a tool returned, then checks all the text a model
sends: its answer, and any text beside a tool call. Text containing a figure it
cannot trace back is held back. The user is told only that an answer was held
back, never the figures in it; what was held back and why is logged and kept on
the plugin for investigation, and never sent to the client. Streamed chunks
would reach the client before the answer is whole, so their text is dropped and
only the checked answer is sent. Thinking is not checked.

What counts as the same figure:
- an exact match: 184210 for 184210;
- a rounding of a traced figure to the precision written: "₹1,84,210" for
  184210.40, "2.5" for 2.47, and never more than 10% away from it, so "₹2 lakh"
  may stand for ₹1,84,210 but "₹1 crore" never stands for ₹50 lakh;
- a rounding in thousands, lakh or crore, in English or Hindi: "1.84 lakh" or
  "₹1.84 लाख" for 184210, "₹38k" for 38210;
- the same size without its sign: "790" for -790, but "-790" only for -790;
- a number written in English words, read as its figure: "forty-two" is 42 and
  "two lakh" is 2 lakh;
- a date or time only if a tool gave that same date or time, whether written
  "13/02/2026", "2026-02-13", "13 February 2026" or "13 Feb", "09:15" or "9:15 am";
  a plain whole number may still name part of one, as in "the 13th";
- a batch-number-like token ("AZ4021") only if it appears exactly as written.

A tool call vouches only for what it found. The arguments the model gave it do
not count, nor does a result repeating one of them back under the same name, so
a figure cannot be laundered through a tool, and a lookup that finds nothing
("no recall notice for AZ4O21") vouches for nothing. A call that fails, returning
an ``error``, adds nothing at all.

Whole numbers up to ``structural_limit`` (10 by default) are always allowed,
because they number the lines of a brief and count small things in prose. That
is a real gap — a model could invent "3 batches" — accepted so the guard does not
block every numbered list. Rupee amounts, negative numbers, dates and everything
above the limit are checked.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from google.adk.models.llm_response import LlmResponse
from google.adk.plugins import BasePlugin
from google.genai import types

_THOUSAND, _LAKH, _CRORE = Decimal(1000), Decimal(100_000), Decimal(10_000_000)
_SCALES = {
    "hundred": Decimal(100),
    "k": _THOUSAND,
    "thousand": _THOUSAND,
    "hazar": _THOUSAND,
    "hazaar": _THOUSAND,
    "हजार": _THOUSAND,
    "l": _LAKH,
    "lakh": _LAKH,
    "lac": _LAKH,
    "लाख": _LAKH,
    "mn": Decimal(1_000_000),
    "million": Decimal(1_000_000),
    "cr": _CRORE,
    "crore": _CRORE,
    "करोड": _CRORE,
    "bn": Decimal(1_000_000_000),
    "billion": Decimal(1_000_000_000),
}
_SCALE_WORD = (
    r"(?i:lakhs?|lacs?|crores?|crs?|thousands?|hundreds?|millions?|mn|billions?|bn|hazaa?r)"
    r"(?![A-Za-z])|लाख|करो(?:ड़?|ड़)|ह(?:ज़?|ज़)ार"
)
_FIGURE = (
    r"(?:(?<![A-Za-z0-9])(?P<sign>[-\u2212]))?"
    r"(?P<money>₹\s?|(?<![A-Za-z])(?:Rs|rs|INR)\.?\s?)?"
    r"(?P<whole>\d{1,3}(?:,\d{2,3})+|\d+)(?P<fraction>\.\d+)?"
    rf"(?:\s*(?P<scale>{_SCALE_WORD})|(?P<short>[kKL])(?![A-Za-z0-9])"
    r"|(?:[a-z]+|(?i:mcg|mg|ml|gm|kg|iu))(?![A-Za-z0-9])|(?![A-Za-z0-9]))"
)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH = (
    r"(?i:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![A-Za-z])"
)
_ISO_DATE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_DAY_MONTH_YEAR = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})")
_MONTH_YEAR = re.compile(r"(\d{1,2})[-/](\d{4})")
_DAY_NAMED_MONTH = re.compile(
    rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH})\.?,?\s+(\d{{4}})"
)
_NAMED_MONTH_DAY = re.compile(rf"({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})")
_NAMED_MONTH = re.compile(rf"({_MONTH})\.?,?\s+(\d{{4}})")
_DAY_OF_MONTH = re.compile(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH})\.?")
_MONTH_DAY = re.compile(rf"({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?")
_TIME = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s?([AaPp])\.?[Mm]\.?)?")
# Without a year, only a capitalised month counts, so "12 may expire" is not a date.
_CAPITAL_MONTH = (
    r"(?:J(?i:an(?:uary)?|une?|uly?)|F(?i:eb(?:ruary)?)|M(?i:ar(?:ch)?|ay)|A(?i:pr(?:il)?"
    r"|ug(?:ust)?)|S(?i:ep(?:t(?:ember)?)?)|O(?i:ct(?:ober)?)|N(?i:ov(?:ember)?)"
    r"|D(?i:ec(?:ember)?))(?![A-Za-z])"
)
_DATE = "|".join(
    [
        r"(?<![\d/.:-])(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}"
        r"|\d{1,2}/\d{1,2}/\d{2}|\d{1,2}[-/]\d{4})(?![/.:-]?\d)",
        rf"(?<!\d)\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\.?,?\s+\d{{4}}(?!\d)",
        rf"(?<![A-Za-z]){_MONTH}\.?(?:\s+\d{{1,2}}(?:st|nd|rd|th)?,?)?,?\s+\d{{4}}(?!\d)",
        rf"(?<!\d)\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_CAPITAL_MONTH}\.?",
        rf"(?<![A-Za-z]){_CAPITAL_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?![A-Za-z0-9])",
        r"(?<![\d:.])\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp]\.?[Mm]\.?(?![A-Za-z]))?(?![\d:])",
    ]
)
_CODE = (
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)"
    r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"
)
_UNIT_WORDS = {
    word: number
    for number, word in enumerate((
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
        "eighteen", "nineteen",
    ))
}  # fmt: skip
_TENS_WORDS = {
    word: 10 * number
    for number, word in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], start=2
    )
}
_SCALE_WORDS = {
    "hundred": 100,
    "thousand": 1_000,
    "lakh": 100_000,
    "lac": 100_000,
    "million": 1_000_000,
    "crore": 10_000_000,
}
_COUNT_WORD = "|".join(sorted([*_UNIT_WORDS, *_TENS_WORDS], key=len, reverse=True))
_WORDS = (
    rf"(?<![A-Za-z])(?i:(?:{_COUNT_WORD})(?:(?:\s+and\s+|[\s-]+)"
    rf"(?:{_COUNT_WORD}|hundred|thousand|lakhs?|lacs?|millions?|crores?))*)(?![A-Za-z])"
)
_TOKEN = re.compile(
    rf"(?P<date>{_DATE})|(?P<figure>{_FIGURE})|(?P<code>{_CODE})|(?P<words>{_WORDS})"
)


@dataclass(frozen=True, slots=True)
class Figure:
    raw: str
    number: Decimal
    """The number as written, with its sign, before any scale such as lakh or crore."""
    decimals: int
    scale: Decimal
    money: bool = False
    """Written as a rupee amount, so never too small to check."""

    @property
    def value(self) -> Decimal:
        return self.number * self.scale


@dataclass(frozen=True, slots=True)
class Moment:
    """A date, a month, a day of a month or a time of day."""

    raw: str
    key: str
    """The same moment however it was written: YYYY-MM-DD, YYYY-MM, --MM-DD or HH:MM."""
    parts: tuple[int, ...]

    @property
    def keys(self) -> set[str]:
        """This moment, and the month, day of the month or minute it falls in."""
        if len(self.key) == 10:
            return {self.key, self.key[:7], "--" + self.key[5:]}
        if self.key.count(":") == 2:
            return {self.key, self.key[:5]}
        return {self.key}


def extract_figures(text: str) -> list[Figure]:
    """Numbers in text, with Indian digit grouping, rupees, signs and scales understood."""
    return [token for token in _scan(text) if isinstance(token, Figure)]


def extract_dates(text: str) -> list[Moment]:
    """Dates, expiry months and times of day in text, each read as a whole."""
    return [token for token in _scan(text) if isinstance(token, Moment)]


def extract_batch_tokens(text: str) -> set[str]:
    """Codes that look like batch numbers: letters and digits mixed, in any case.

    Look-alike characters are deliberately not required to fall in any order, so a
    misread "AZ4O21" is still recognised, and then fails to match "AZ4021" (ADR 0002).
    """
    return {token for token in _scan(text) if isinstance(token, str)}


def _scan(text: str) -> Iterator[Figure | Moment | str]:
    for match in _TOKEN.finditer(text):
        raw = match.group(0)
        if match["date"]:
            moment = _moment(raw)
            yield from [moment] if moment else _digits(raw)
        elif match["figure"]:
            yield _figure(match)
        elif match["words"]:
            yield _word_figure(raw)
        elif _batch_like(raw):
            yield raw
        else:
            yield from _digits(raw)


def _figure(match: re.Match[str]) -> Figure:
    fraction = match["fraction"] or ""
    number = Decimal(match["whole"].replace(",", "") + fraction)
    word = match["scale"] or match["short"]
    return Figure(
        raw=match.group(0).strip(),
        number=-number if match["sign"] else number,
        decimals=max(0, len(fraction) - 1),
        scale=_SCALES[_scale_key(word)] if word else Decimal(1),
        money=bool(match["money"]),
    )


def _word_figure(raw: str) -> Figure:
    """A number written in words, kept in the largest round unit it names.

    "two lakh" is 2 lakh, so it is checked as a rounding in lakh, just as "2 lakh" is.
    """
    total = current = 0
    scales = []
    for word in re.findall(r"[a-z]+", raw.lower()):
        if word in _UNIT_WORDS:
            current += _UNIT_WORDS[word]
        elif word in _TENS_WORDS:
            current += _TENS_WORDS[word]
        elif word != "and":
            scale = _SCALE_WORDS[word.removesuffix("s")]
            scales.append(scale)
            if scale == 100:
                current = (current or 1) * 100
            else:
                total, current = total + (current or 1) * scale, 0
    value = total + current
    scale = min((s for s in scales if value % s == 0), default=1)
    return Figure(raw, Decimal(value // scale), 0, Decimal(scale))


def _scale_key(word: str) -> str:
    key = unicodedata.normalize("NFD", word).replace("़", "").lower()
    return key if key in _SCALES else key.removesuffix("s")


def _digits(raw: str) -> Iterator[Figure]:
    """The numbers in something that looked like a date or code but was not one."""
    for digits in re.findall(r"\d+", raw):
        yield Figure(digits, Decimal(digits), 0, Decimal(1))


def _batch_like(token: str) -> bool:
    pieces = token.split("-")
    characters = "".join(pieces)
    return (
        len(characters) >= 4
        and sum(ch.isdigit() for ch in characters) >= 2
        and any(
            any(ch.isalpha() for ch in piece) and any(ch.isdigit() for ch in piece)
            for piece in pieces
        )
    )


def _moment(raw: str) -> Moment | None:
    """The date or time ``raw`` names, or None if it names no real one."""
    try:
        if found := _ISO_DATE.fullmatch(raw):
            year, month, day = (int(part) for part in found.groups())
        elif found := _DAY_MONTH_YEAR.fullmatch(raw):
            day, month, year = (int(part) for part in found.groups())
            year += 2000 if year < 100 else 0
        elif found := _DAY_NAMED_MONTH.fullmatch(raw):
            day, month, year = int(found[1]), _month(found[2]), int(found[3])
        elif found := _NAMED_MONTH_DAY.fullmatch(raw):
            day, month, year = int(found[2]), _month(found[1]), int(found[3])
        elif found := _MONTH_YEAR.fullmatch(raw) or _NAMED_MONTH.fullmatch(raw):
            month, year = int(found[1]) if found[1].isdigit() else _month(found[1]), int(found[2])
            date(year, month, 1)
            return Moment(raw, f"{year:04d}-{month:02d}", (year, month))
        elif found := _DAY_OF_MONTH.fullmatch(raw):
            return _day_of_month(raw, int(found[1]), _month(found[2]))
        elif found := _MONTH_DAY.fullmatch(raw):
            return _day_of_month(raw, int(found[2]), _month(found[1]))
        else:
            return _time(raw)
        date(year, month, day)
    except ValueError:
        return None
    return Moment(raw, f"{year:04d}-{month:02d}-{day:02d}", (year, month, day))


def _day_of_month(raw: str, day: int, month: int) -> Moment:
    date(2000, month, day)
    return Moment(raw, f"--{month:02d}-{day:02d}", (month, day))


def _time(raw: str) -> Moment | None:
    found = _TIME.fullmatch(raw)
    if not found:
        return None
    hour, minute = int(found[1]), int(found[2])
    second = None if found[3] is None else int(found[3])
    if found[4]:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if found[4] in "Pp" else 0)
    if hour > 23 or minute > 59 or (second or 0) > 59:
        return None
    key = f"{hour:02d}:{minute:02d}" + ("" if second is None else f":{second:02d}")
    return Moment(raw, key, (hour, minute) if second is None else (hour, minute, second))


def _month(name: str) -> int:
    return _MONTHS.index(name[:3].lower()) + 1


@dataclass(slots=True)
class Evidence:
    """Figures, dates and batch tokens that an answer in this run may state."""

    values: set[Decimal] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)
    date_parts: set[Decimal] = field(default_factory=set)
    """Years, days, hours and so on, which only a plain whole number such as "the 13th" may use."""
    batch_tokens: set[str] = field(default_factory=set)

    def add_text(self, text: str) -> None:
        for token in _scan(text):
            if isinstance(token, Figure):
                self.values.add(token.value)
            elif isinstance(token, Moment):
                self.dates.update(token.keys)
                self.date_parts.update(Decimal(part) for part in token.parts)
            else:
                self.batch_tokens.add(token)

    def add_tool_call(self, args: Any, result: Any) -> None:
        """Record what a tool call found: not its arguments, nor the result repeating them."""
        if isinstance(result, dict):
            if "error" in result:
                return
            if isinstance(args, dict):
                result = {
                    key: value
                    for key, value in result.items()
                    if not (key in args and _same(value, args[key]))
                }
        self.add_result(result)

    def add_result(self, result: Any) -> None:
        """Record every number and every number inside a string, however deeply nested."""
        if isinstance(result, bool) or result is None:
            return
        if isinstance(result, int | float | Decimal):
            self.values.add(Decimal(str(result)))
        elif isinstance(result, str):
            self.add_text(result)
        elif isinstance(result, dict):
            for key, value in result.items():
                self.add_result(key)
                self.add_result(value)
        elif isinstance(result, Iterable):
            for value in result:
                self.add_result(value)


def _same(value: Any, argument: Any) -> bool:
    """Whether a result field repeats an argument, as tools normalise case and spacing."""
    if isinstance(value, str) and isinstance(argument, str):
        return "".join(value.split()).casefold() == "".join(argument.split()).casefold()
    return value == argument


def unsupported(text: str, evidence: Evidence, *, structural_limit: int = 10) -> list[str]:
    """Every figure, date or batch token in ``text`` that the evidence does not support."""
    problems = []
    sizes = {abs(value) for value in evidence.values}
    for token in _scan(text):
        if isinstance(token, Moment):
            supported = token.key in evidence.dates
        elif isinstance(token, Figure):
            supported = (
                _plain(token)
                and (token.number <= structural_limit or token.number in evidence.date_parts)
            ) or _traced(token, evidence.values if token.number < 0 else sizes)
        else:
            supported = token in evidence.batch_tokens
        if not supported:
            problems.append(token if isinstance(token, str) else token.raw)
    return list(dict.fromkeys(problems))


def _plain(figure: Figure) -> bool:
    """A whole number with no sign, rupee symbol or scale."""
    return not figure.money and figure.scale == 1 and figure.decimals == 0 and figure.number >= 0


ROUNDING_TOLERANCE = Decimal("0.10")
"""The furthest a rounded figure may be from the figure it rounds, as a fraction of it."""


def _traced(figure: Figure, values: set[Decimal]) -> bool:
    if figure.value in values:
        return True
    places = Decimal(1).scaleb(-figure.decimals)
    for value in values:
        try:
            rounded = (value / figure.scale).quantize(places, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            continue
        if rounded == figure.number and abs(figure.value - value) <= ROUNDING_TOLERANCE * abs(
            value
        ):
            return True
    return False


HELD_BACK = (
    "I couldn't match every figure in my answer to the stock data, so I've held it back "
    "rather than risk a wrong number. Please ask again."
)
"""What the user sees instead. It names no figure: a figure named here would reach them."""

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HeldBack:
    """Text the guard held back, kept on the server for investigation."""

    invocation_id: str
    text: str
    unsupported: tuple[str, ...]


def _is_answer(part: types.Part) -> bool:
    return bool(part.text) and not part.thought


class NumbersGuardPlugin(BasePlugin):
    """Holds back any text stating a figure no tool returned in the same run."""

    def __init__(self, *, structural_limit: int = 10, keep: int = 100) -> None:
        super().__init__(name="numbers_guard")
        self.structural_limit = structural_limit
        self._evidence: dict[str, Evidence] = {}
        self.held_back: deque[HeldBack] = deque(maxlen=keep)
        """The most recent text held back, newest last. It stays in this process."""

    def evidence_for(self, invocation_id: str) -> Evidence:
        return self._evidence.setdefault(invocation_id, Evidence())

    async def on_user_message_callback(self, *, invocation_context, user_message: types.Content):
        evidence = self.evidence_for(invocation_context.invocation_id)
        for part in user_message.parts or []:
            if part.text:
                evidence.add_text(part.text)
        return None

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        self.evidence_for(tool_context.invocation_id).add_tool_call(tool_args, result)
        return None

    async def after_model_callback(self, *, callback_context, llm_response: LlmResponse):
        parts = (llm_response.content.parts if llm_response.content else None) or []
        if not any(_is_answer(part) for part in parts):
            return None
        if llm_response.partial:
            # ADK sends each streamed chunk to the client as it arrives. The whole answer
            # follows as one final response, which is checked below.
            rest = [part for part in parts if not _is_answer(part)]
            content = types.Content(role="model", parts=rest) if rest else None
            return llm_response.model_copy(update={"content": content})
        text = "".join(part.text for part in parts if _is_answer(part))
        problems = unsupported(
            text,
            self.evidence_for(callback_context.invocation_id),
            structural_limit=self.structural_limit,
        )
        if not problems:
            return None
        # Beside a tool call the text is only dropped, so the call still goes ahead.
        kept = [part for part in parts if part.function_call] or [types.Part(text=HELD_BACK)]
        invocation_id = callback_context.invocation_id
        self.held_back.append(HeldBack(invocation_id, text, tuple(problems)))
        _log.warning(
            "Numbers Guard held back text in invocation %s: no tool supports %s",
            invocation_id,
            ", ".join(problems),
        )
        metadata = {"numbers_guard": {"held_back": True}}
        return llm_response.model_copy(
            update={
                "content": types.Content(role="model", parts=kept),
                "custom_metadata": {**(llm_response.custom_metadata or {}), **metadata},
            }
        )

    async def after_run_callback(self, *, invocation_context) -> None:
        self._evidence.pop(invocation_context.invocation_id, None)
