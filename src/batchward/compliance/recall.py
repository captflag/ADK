"""Recall notices: which batches they name, and blocking those batches at once.

A notice arrives as text — a company's recall letter, or a line in CDSCO's
monthly list of drugs not of standard quality — naming a product, its
manufacturer, a batch number and an expiry. Matching it to stock is where a
recall goes wrong in both directions: miss the batch and a recalled drug keeps
selling; match loosely and good stock is frozen.

So matching is strict (ADR 0002, ADR 0004):

- The batch number must be identical once spaces and letter case are ignored.
- A match is **exact** only when the notice also gives the manufacturer, the
  product and the expiry month, and all three agree with the batch. Exact
  matches are blocked immediately and automatically.
- A batch with the same number but a missing or disagreeing detail, or a batch
  whose number differs only in look-alike characters (AZ4021 and AZ4O21), is
  raised for a person to review. It is never blocked automatically.

Deadlines follow CDSCO's recall classes. The clocks start when the distributor
receives the notice, which is the earliest moment it can act and so the
strictest reading.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from batchward.core.holds import Hold, HoldLog
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, BatchStatus, Item, Party


class RecallClass(StrEnum):
    I = "I"  # noqa: E741 - CDSCO names the classes I, II and III
    II = "II"
    III = "III"


@dataclass(frozen=True, slots=True)
class RecallClock:
    stop_sale: timedelta | None
    """How soon sale must stop, where the guideline sets a separate limit."""
    complete: timedelta
    """How soon the recall must be complete."""


RECALL_CLOCKS: Mapping[RecallClass, RecallClock] = {
    RecallClass.I: RecallClock(stop_sale=timedelta(hours=24), complete=timedelta(hours=72)),
    RecallClass.II: RecallClock(stop_sale=None, complete=timedelta(days=10)),
    RecallClass.III: RecallClock(stop_sale=None, complete=timedelta(days=30)),
}
RECALL_CLOCKS_SOURCE = "CDSCO guidelines on recall and rapid alert system for drugs"


@dataclass(frozen=True, slots=True)
class RecallNotice:
    reference: str
    source: str
    """Who issued it, e.g. the manufacturer or CDSCO."""
    recall_class: RecallClass
    received_at: datetime
    batch_no: str
    """As printed in the notice."""
    manufacturer: str | None = None
    product: str | None = None
    expiry: date | None = None
    """Only the month and year count; notices print expiry as MM/YYYY, so it is kept as the
    last day of its month."""

    def __post_init__(self) -> None:
        if self.expiry is not None:
            last = calendar.monthrange(self.expiry.year, self.expiry.month)[1]
            object.__setattr__(self, "expiry", self.expiry.replace(day=last))
        if self.received_at.tzinfo is None:
            raise ValueError("received_at must be timezone-aware")
        if not "".join(self.batch_no.split()):
            raise ValueError("a notice must name a batch number")
        if not self.reference.strip():
            raise ValueError("a notice needs a reference")
        if not self.source.strip():
            raise ValueError("a notice must say who issued it")

    @property
    def clock(self) -> RecallClock:
        return RECALL_CLOCKS[self.recall_class]


class MatchKind(StrEnum):
    EXACT = "exact"
    PARTIAL = "same batch number, other details missing or different"
    LOOK_ALIKE = "batch number differs only in look-alike characters"


@dataclass(frozen=True, slots=True)
class Candidate:
    batch: BatchKey
    kind: MatchKind
    reasons: tuple[str, ...]
    """Why this is not an exact match; empty for an exact one."""


@dataclass(frozen=True, slots=True)
class NoticeMatch:
    notice: RecallNotice
    exact: tuple[BatchKey, ...]
    """Batches to block now."""
    review: tuple[Candidate, ...]
    """Batches a person must look at before anything happens to them."""


def batches_ever_held(ledger: Ledger) -> set[BatchKey]:
    return {movement.batch for movement in ledger}


def match_notice(
    notice: RecallNotice,
    batches: Iterable[BatchKey],
    *,
    items: Mapping[str, Item],
    parties: Mapping[str, Party],
) -> NoticeMatch:
    """Compare a notice with batches, normally every batch ever held."""
    wanted = "".join(notice.batch_no.split()).upper()
    folded = _fold(wanted)
    exact: list[BatchKey] = []
    review: list[Candidate] = []
    for key in sorted(set(batches)):
        if key.batch_no == wanted:
            reasons = _disagreements(notice, key, items, parties)
            if reasons:
                review.append(Candidate(key, MatchKind.PARTIAL, reasons))
            else:
                exact.append(key)
        elif folded and _fold(key.batch_no) == folded:
            review.append(
                Candidate(
                    key,
                    MatchKind.LOOK_ALIKE,
                    (f"batch {key.batch_no} is not {wanted}, but is easily misread as it",),
                )
            )
    return NoticeMatch(notice=notice, exact=tuple(exact), review=tuple(review))


@dataclass(frozen=True, slots=True)
class Recall:
    notice: RecallNotice
    match: NoticeMatch
    holds: tuple[Hold, ...]
    """Holds this recall placed. Empty when every exact match was already blocked for it."""

    @property
    def stop_sale_due(self) -> datetime | None:
        limit = self.notice.clock.stop_sale
        return None if limit is None else self.notice.received_at + limit

    @property
    def complete_due(self) -> datetime:
        return self.notice.received_at + self.notice.clock.complete


def open_recall(
    notice: RecallNotice,
    *,
    ledger: Ledger,
    items: Mapping[str, Item],
    parties: Mapping[str, Party],
    holds: HoldLog,
    at: datetime,
    batches: Iterable[BatchKey] = (),
) -> Recall:
    """Match a notice against every batch ever held and block exact matches at once.

    This is the one action Batchward takes without asking (ADR 0004). Blocking
    is recorded as a hold placed by ``"system"`` at ``at``, the moment it is
    actually placed, so a late block shows as late. A person can release it.
    ``batches`` adds batches on record with no movement, such as opening stock a
    billing system holds without a bill. Processing the same notice again places
    no second hold, and never blocks again a batch a person released.
    """
    if at < notice.received_at:
        raise ValueError("a recall cannot be acted on before its notice was received")
    candidates = batches_ever_held(ledger) | set(batches)
    match = match_notice(notice, candidates, items=items, parties=parties)
    placed = []
    for key in match.exact:
        if any(hold.reference == notice.reference for hold in holds.holds_for(key)):
            continue
        placed.append(
            holds.place(
                key,
                BatchStatus.BLOCKED,
                at=at,
                reason=f"Class {notice.recall_class} recall notice from {notice.source}",
                reference=notice.reference,
                placed_by="system",
            )
        )
    return Recall(notice=notice, match=match, holds=tuple(placed))


def _disagreements(
    notice: RecallNotice,
    key: BatchKey,
    items: Mapping[str, Item],
    parties: Mapping[str, Party],
) -> tuple[str, ...]:
    reasons: list[str] = []

    company = parties.get(key.company_id)
    if notice.manufacturer is None:
        reasons.append("the notice does not name the manufacturer")
    elif company is None:
        reasons.append(f"no company record for {key.company_id}")
    elif not _mentions(notice.manufacturer, company.name):
        reasons.append(
            f"the notice names the manufacturer {notice.manufacturer!r}, "
            f"but the batch is from {company.name}"
        )

    if notice.expiry is None:
        reasons.append("the notice does not give the expiry")
    elif (notice.expiry.year, notice.expiry.month) != (key.expiry.year, key.expiry.month):
        reasons.append(
            f"the notice gives expiry {notice.expiry:%m/%Y}, but the batch expires "
            f"{key.expiry:%m/%Y}"
        )

    item = items.get(key.item_id)
    if notice.product is None:
        reasons.append("the notice does not name the product")
    elif item is None:
        reasons.append(f"no item record for {key.item_id}")
    elif not _names_item(notice.product, item):
        reasons.append(
            f"the notice names {notice.product!r}, but the batch is {item.brand} "
            f"({item.molecule} {item.strength})"
        )

    return tuple(reasons)


_TOKEN = re.compile(r"\d+(?:\.\d+)?|%|[a-z]+(?:-[a-z]+)*")
_UNITS = frozenset({"mg", "mcg", "ug", "g", "gm", "gms", "kg", "ml", "l", "iu", "%"})


def _tokens(text: str | None) -> list[str]:
    """Words and numbers, ignoring case and punctuation: "2.5mg" -> ["2.5", "mg"].

    A decimal stays one number, so 2.5 mg is never read as 5 mg, and a hyphenated
    word stays one word, so S-Amlodipine is not Amlodipine.
    """
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    return [
        format(Decimal(token).normalize(), "f") if token[0].isdigit() else token
        for token in _TOKEN.findall(text)
    ]


def _contains(words: list[str], phrase: list[str]) -> bool:
    """Whether a non-empty phrase appears in ``words`` as consecutive whole words."""
    size = len(phrase)
    return size > 0 and any(words[i : i + size] == phrase for i in range(len(words) - size + 1))


def _strengths(words: list[str]) -> set[tuple[str, str]]:
    """Every amount with its unit, such as ("40", "mg")."""
    return {
        (number, unit) for number, unit in pairwise(words) if number[0].isdigit() and unit in _UNITS
    }


def _mentions(text: str, phrase: str) -> bool:
    """Whether ``phrase`` appears in ``text`` as whole words, ignoring case and punctuation."""
    return _contains(_tokens(text), _tokens(phrase))


def _names_item(product: str, item: Item) -> bool:
    """The brand is named, or the molecule with its strength, and any strength given is the item's.

    A product naming a strength the item does not have — a combination with a
    second molecule, or another strength of the same one — is not this item.
    """
    words = _tokens(product)
    named = _contains(words, _tokens(item.brand)) or (
        _contains(words, _tokens(item.molecule)) and _contains(words, _tokens(item.strength))
    )
    stated = _strengths(words)
    return named and (not stated or stated == _strengths(_tokens(item.strength)))


_LOOK_ALIKES = str.maketrans(
    {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"}
)
_LATIN_TWINS = str.maketrans(
    {
        chr(code): latin
        for codes, latins in (
            # Cyrillic
            (
                (0x410, 0x412, 0x415, 0x41A, 0x41C, 0x41D, 0x41E, 0x420, 0x421, 0x422, 0x423),
                "ABEKMHOPCTY",
            ),
            ((0x425, 0x406, 0x408, 0x405), "XIJS"),
            # Greek
            ((0x391, 0x392, 0x395, 0x396, 0x397, 0x399, 0x39A, 0x39C, 0x39D, 0x39F), "ABEZHIKMNO"),
            ((0x3A1, 0x3A4, 0x3A5, 0x3A7), "PTYX"),
        )
        for code, latin in zip(codes, latins, strict=True)
    }
)
"""Cyrillic and Greek capitals printed identically to Latin ones."""


def looks_alike(batch_no: str, other: str) -> bool:
    """Whether two different batch numbers are easily misread as each other: AZ4021 and AZ4O21."""
    wanted, folded = "".join(batch_no.split()).upper(), _fold(batch_no)
    return bool(folded) and "".join(other.split()).upper() != wanted and _fold(other) == folded


def _fold(batch_no: str) -> str:
    """A batch number with look-alike characters and punctuation collapsed, for review only.

    Full-width characters and Cyrillic or Greek letters that look Latin are folded
    too, so a notice typed on another keyboard still raises the batch for review.
    """
    text = unicodedata.normalize("NFKC", batch_no).upper().translate(_LATIN_TWINS)
    return re.sub(r"[^A-Z0-9]", "", text).translate(_LOOK_ALIKES)
