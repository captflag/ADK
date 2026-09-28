"""The fonts a PDF page uses, read for two things: what a code means, and how wide it is.

A PDF prints codes, not characters. What a code means is whatever its font
says: a byte through an encoding for an ordinary font, or a two-byte identifier
through the font's own table for the fonts a typesetter subsets. Most documents
carry a ``/ToUnicode`` table that says outright, and that is trusted first. A
code with nothing to say for it is read as the replacement character so that a
row is never quietly shortened; the reader counts those and says so.

Widths matter as much as characters: where a line of a table ends decides which
column the next piece of text belongs to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from batchward.documents.pdf import END, Document, Keyword, Lexer, Name, Stream

UNREADABLE = "�"
"""What a code with no character behind it reads as."""
DEFAULT_WIDTH = 500.0
"""A thousandth of the font size, for a font that gives no width at all."""

_BASE_ENCODINGS = {
    "WinAnsiEncoding": "cp1252",
    "MacRomanEncoding": "mac_roman",
    "PDFDocEncoding": "latin-1",
    "StandardEncoding": "latin-1",
}
_GLYPH_NAMES = {
    "space": " ",
    "exclam": "!",
    "quotedbl": '"',
    "numbersign": "#",
    "dollar": "$",
    "percent": "%",
    "ampersand": "&",
    "quotesingle": "'",
    "quoteright": "’",
    "quoteleft": "‘",
    "quotedblleft": "“",
    "quotedblright": "”",
    "parenleft": "(",
    "parenright": ")",
    "asterisk": "*",
    "plus": "+",
    "comma": ",",
    "hyphen": "-",
    "period": ".",
    "slash": "/",
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "colon": ":",
    "semicolon": ";",
    "less": "<",
    "equal": "=",
    "greater": ">",
    "question": "?",
    "at": "@",
    "bracketleft": "[",
    "backslash": "\\",
    "bracketright": "]",
    "asciicircum": "^",
    "underscore": "_",
    "grave": "`",
    "braceleft": "{",
    "bar": "|",
    "braceright": "}",
    "asciitilde": "~",
    "endash": "–",
    "emdash": "—",
    "bullet": "•",
    "rupee": "₹",
    "Euro": "€",
    "degree": "°",
    "percentsign": "%",
    "nbspace": " ",
    "fi": "fi",
    "fl": "fl",
}
"""Glyph names a ``/Differences`` list uses that are not simply a letter."""


@dataclass(frozen=True, slots=True)
class Font:
    """One font as the reader needs it: codes in, characters and widths out."""

    name: str
    two_byte: bool
    """Identifiers of two bytes, as a subset font written with Identity-H uses."""
    characters: dict[int, str]
    widths: dict[int, float]
    """Thousandths of the font size."""
    default_width: float = DEFAULT_WIDTH

    def read(self, codes: bytes) -> list[tuple[int, str]]:
        """A printed string as the codes it holds and what each one means."""
        if self.two_byte:
            if len(codes) % 2:
                codes += b"\x00"
            numbers = [int.from_bytes(codes[at : at + 2], "big") for at in range(0, len(codes), 2)]
        else:
            numbers = list(codes)
        return [(code, self.characters.get(code, UNREADABLE)) for code in numbers]

    def width(self, code: int) -> float:
        return self.widths.get(code, self.default_width)


def load_font(document: Document, holder: object, name: str) -> Font:
    """The font a page's resources give this name."""
    font = document.resolve(holder)
    if not isinstance(font, dict):
        return Font(name=name, two_byte=False, characters={}, widths={})
    subtype = str(document.entry(font, "Subtype", ""))
    characters = _to_unicode(document, font)
    if subtype == "Type0":
        return _composite(document, font, name, characters)
    encoded = _simple_characters(document, font)
    encoded.update(characters)  # what the font says outright wins over its encoding
    return Font(
        name=name,
        two_byte=False,
        characters=encoded,
        widths=_simple_widths(document, font),
        default_width=float(document.entry(_descriptor(document, font), "MissingWidth", 0.0) or 0.0)
        or DEFAULT_WIDTH,
    )


def _composite(
    document: Document, font: dict[str, object], name: str, characters: dict[int, str]
) -> Font:
    descendants = document.entry(font, "DescendantFonts", [])
    listed = descendants if isinstance(descendants, list) else []
    descendant = document.resolve(listed[0]) if listed else None
    widths: dict[int, float] = {}
    default = 1000.0
    if isinstance(descendant, dict):
        default = float(document.entry(descendant, "DW", 1000.0) or 1000.0)
        widths = _cid_widths(document, document.entry(descendant, "W", []))
    encoding = document.entry(font, "Encoding")
    named = str(encoding) if isinstance(encoding, Name) else ""
    one_byte = bool(named) and "Identity" not in named and "UCS2" not in named
    return Font(
        name=name,
        two_byte=not one_byte,
        characters=characters,
        widths=widths,
        default_width=default,
    )


def _descriptor(document: Document, font: dict[str, object]) -> object:
    return document.entry(font, "FontDescriptor", {})


def _simple_widths(document: Document, font: dict[str, object]) -> dict[int, float]:
    first = document.entry(font, "FirstChar", 0)
    listed = document.entry(font, "Widths", [])
    if not isinstance(listed, list) or not isinstance(first, int):
        return {}
    scale = 1.0
    matrix = document.entry(font, "FontMatrix")  # a Type 3 font gives its own glyph space
    if isinstance(matrix, list) and matrix and isinstance(matrix[0], int | float) and matrix[0]:
        scale = float(matrix[0]) * 1000
    widths = {}
    for offset, width in enumerate(listed):
        width = document.resolve(width)
        if isinstance(width, int | float):
            widths[first + offset] = float(width) * scale
    return widths


def _cid_widths(document: Document, listed: object) -> dict[int, float]:
    """The ``/W`` array: ``code [w w w]`` for a run, or ``first last w`` for a range."""
    if not isinstance(listed, list):
        return {}
    values = [document.resolve(value) for value in listed]
    widths: dict[int, float] = {}
    at = 0
    while at < len(values):
        first = values[at]
        if not isinstance(first, int) or at + 1 >= len(values):
            break
        following = values[at + 1]
        if isinstance(following, list):
            for offset, width in enumerate(following):
                width = document.resolve(width)
                if isinstance(width, int | float):
                    widths[first + offset] = float(width)
            at += 2
            continue
        if at + 2 < len(values) and isinstance(following, int):
            width = values[at + 2]
            if isinstance(width, int | float) and following - first < 65536:
                for code in range(first, following + 1):
                    widths[code] = float(width)
            at += 3
            continue
        break
    return widths


def _simple_characters(document: Document, font: dict[str, object]) -> dict[int, str]:
    encoding = document.entry(font, "Encoding")
    base = "latin-1"
    differences: list[object] = []
    if isinstance(encoding, Name):
        base = _BASE_ENCODINGS.get(str(encoding), base)
    elif isinstance(encoding, dict):
        named = document.resolve(encoding.get("BaseEncoding"))
        if isinstance(named, Name):
            base = _BASE_ENCODINGS.get(str(named), base)
        listed = document.resolve(encoding.get("Differences"))
        differences = listed if isinstance(listed, list) else []
    characters = {}
    for code in range(256):
        try:
            characters[code] = bytes([code]).decode(base)
        except UnicodeDecodeError:
            continue
    code = 0
    for item in differences:
        item = document.resolve(item)
        if isinstance(item, int | float):
            code = int(item)
        elif isinstance(item, Name):
            character = glyph(str(item))
            if character is not None:
                characters[code] = character
            code += 1
    return characters


def glyph(name: str) -> str | None:
    """What a glyph name means, where its name says so."""
    if len(name) == 1:
        return name
    if name in _GLYPH_NAMES:
        return _GLYPH_NAMES[name]
    if found := re.fullmatch(r"uni([0-9A-Fa-f]{4})", name):
        return chr(int(found[1], 16))
    if found := re.fullmatch(r"u([0-9A-Fa-f]{4,6})", name):
        return chr(int(found[1], 16))
    return None


def _to_unicode(document: Document, font: dict[str, object]) -> dict[int, str]:
    stream = document.resolve(font.get("ToUnicode"))
    if not isinstance(stream, Stream):
        return {}
    try:
        return read_cmap(document.data(stream))
    except ValueError:
        return {}


MOST_ENTRIES = 200_000
"""A ``/ToUnicode`` table longer than this is not a table of characters."""


def read_cmap(data: bytes) -> dict[int, str]:
    """A ``/ToUnicode`` table: what each code prints, by ``bfchar`` and ``bfrange``."""
    characters: dict[int, str] = {}
    lexer = Lexer(data)
    collecting: list[object] | None = None
    kind = ""
    while (item := lexer.read()) is not END:
        if isinstance(item, Keyword):
            word = str(item)
            if word in ("beginbfchar", "beginbfrange"):
                collecting, kind = [], word
                continue
            if collecting is not None and word in ("endbfchar", "endbfrange"):
                if kind == "beginbfchar":
                    _read_pairs(collecting, characters)
                else:
                    _read_ranges(collecting, characters)
                collecting = None
                continue
        if collecting is not None:
            collecting.append(item)
            if len(collecting) > MOST_ENTRIES:  # pragma: no cover - not a table of characters
                collecting = None
    return characters


def _read_pairs(items: list[object], characters: dict[int, str]) -> None:
    for at in range(0, len(items) - 1, 2):
        code, value = items[at], items[at + 1]
        if isinstance(code, bytes):
            text = _text(value)
            if text is not None:
                characters[int.from_bytes(code, "big")] = text


def _read_ranges(items: list[object], characters: dict[int, str]) -> None:
    for at in range(0, len(items) - 2, 3):
        low, high, value = items[at], items[at + 1], items[at + 2]
        if not isinstance(low, bytes) or not isinstance(high, bytes):
            continue
        first, last = int.from_bytes(low, "big"), int.from_bytes(high, "big")
        if last < first or last - first > 65536:
            continue
        if isinstance(value, list):
            for offset, item in enumerate(value):
                text = _text(item)
                if text is not None:
                    characters[first + offset] = text
            continue
        text = _text(value)
        if text is None:
            continue
        for offset in range(last - first + 1):
            characters[first + offset] = _shift(text, offset)


def _text(value: object) -> str | None:
    if not isinstance(value, bytes) or not value:
        return None
    if len(value) % 2:
        value += b"\x00"
    try:
        return value.decode("utf-16-be").replace("\x00", "")
    except UnicodeDecodeError:
        return None


def _shift(text: str, offset: int) -> str:
    """The next character along, as a range's destination counts up."""
    if not text or offset == 0:
        return text
    return text[:-1] + chr(ord(text[-1]) + offset)
