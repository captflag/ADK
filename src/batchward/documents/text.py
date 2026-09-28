"""Where every piece of text sits on a PDF page.

A page's content stream is a list of operators that place text on the page: a
matrix that says where and at what size, a font that says what the codes mean,
and strings of codes. Running those operators gives each piece of text a point
on the page, which is what a table needs — a row is text sharing a baseline,
and a column is text sharing a place across the page.

Positions here are in points, measured from the top left of the page as it is
displayed, so that sorting by ``(y, x)`` is reading order however the page is
turned. Pieces are split where the pen jumps a gap wider than a quarter of the
type size, because that gap is how a typesetter draws the space between one
column and the next.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from batchward.documents.glyphs import UNREADABLE, Font, load_font
from batchward.documents.pdf import (
    END,
    Document,
    Keyword,
    Lexer,
    Name,
    PdfError,
    Stream,
)

Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
SPLIT_GAP = 0.25
"""A jump wider than this many type sizes starts a new piece of text."""
LETTER = (612.0, 792.0)
"""The page size assumed when a page gives none."""
MOST_FORMS = 12
"""How deeply one form drawn inside another is followed."""


@dataclass(frozen=True, slots=True)
class Piece:
    """One run of text printed at one place on the page."""

    page: int
    x: float
    """Points from the left edge of the page."""
    y: float
    """Points from the top edge of the page, at the baseline."""
    width: float
    size: float
    """The type size in points, as printed."""
    text: str

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def unreadable(self) -> int:
        return self.text.count(UNREADABLE)


@dataclass(frozen=True, slots=True)
class Page:
    """One page's text layer."""

    number: int
    width: float
    height: float
    pieces: tuple[Piece, ...]

    @property
    def is_scan(self) -> bool:
        """A page with no text layer at all: a picture of a page, or an empty one."""
        return not any(piece.text.strip() for piece in self.pieces)

    @property
    def unreadable(self) -> int:
        return sum(piece.unreadable for piece in self.pieces)


@dataclass(frozen=True, slots=True)
class Reading:
    """Every page of a document, as the text printed on it."""

    pages: tuple[Page, ...]

    @property
    def scans(self) -> tuple[int, ...]:
        """The pages that carry no text, which have to be read by eye."""
        return tuple(page.number for page in self.pages if page.is_scan)

    @property
    def unreadable(self) -> int:
        return sum(page.unreadable for page in self.pages)

    def page(self, number: int) -> Page:
        for page in self.pages:
            if page.number == number:
                return page
        raise PdfError(f"the document has no page {number}")


def read_text(document: Document, *, pages: range | None = None) -> Reading:
    """Every page's text, placed on the page."""
    found = document.pages()
    if not found:
        raise PdfError("the file holds no pages")
    if document.encrypted:
        raise PdfError(
            "the file is encrypted; open it and save a copy without a password, then read that"
        )
    read = []
    for number, page in enumerate(found, start=1):
        if pages is not None and number not in pages:
            continue
        read.append(_read_page(document, page, number))
    return Reading(tuple(read))


def _read_page(document: Document, page: dict[str, object], number: int) -> Page:
    box = _box(document, page)
    rotate = document.entry(page, "Rotate", 0)
    rotate = int(rotate) % 360 if isinstance(rotate, int | float) else 0
    rotate -= rotate % 90
    width, height = box[2] - box[0], box[3] - box[1]
    if rotate in (90, 270):
        width, height = height, width
    painter = _Painter(document, number, box, rotate)
    painter.run(_content(document, page), document.entry(page, "Resources", {}), IDENTITY, 0)
    pieces = sorted(painter.pieces, key=lambda piece: (round(piece.y, 1), piece.x))
    return Page(number=number, width=width, height=height, pieces=tuple(pieces))


def _box(document: Document, page: dict[str, object]) -> tuple[float, float, float, float]:
    for key in ("CropBox", "MediaBox"):
        listed = document.entry(page, key)
        if isinstance(listed, list) and len(listed) == 4:
            values = [document.resolve(value) for value in listed]
            if all(isinstance(value, int | float) for value in values):
                x0, y0, x1, y1 = (float(value) for value in values)  # type: ignore[arg-type]
                if x1 < x0:
                    x0, x1 = x1, x0
                if y1 < y0:
                    y0, y1 = y1, y0
                if x1 - x0 > 1 and y1 - y0 > 1:
                    return x0, y0, x1, y1
    return 0.0, 0.0, LETTER[0], LETTER[1]


def _content(document: Document, page: dict[str, object]) -> bytes:
    contents = document.resolve(page.get("Contents"))
    streams = contents if isinstance(contents, list) else [contents]
    parts = []
    for stream in streams:
        stream = document.resolve(stream)
        if isinstance(stream, Stream):
            try:
                parts.append(document.data(stream))
            except PdfError:
                continue
    return b"\n".join(parts)


@dataclass(slots=True)
class _State:
    """What the operators keep between them, saved and restored by ``q`` and ``Q``."""

    ctm: Matrix = IDENTITY
    font: Font | None = None
    size: float = 0.0
    character_spacing: float = 0.0
    word_spacing: float = 0.0
    horizontal: float = 1.0
    leading: float = 0.0
    rise: float = 0.0

    def copy(self) -> _State:
        return _State(
            self.ctm,
            self.font,
            self.size,
            self.character_spacing,
            self.word_spacing,
            self.horizontal,
            self.leading,
            self.rise,
        )


class _Painter:
    """Runs a content stream and keeps every piece of text it prints."""

    def __init__(
        self,
        document: Document,
        number: int,
        box: tuple[float, float, float, float],
        rotate: int,
    ) -> None:
        self.document = document
        self.number = number
        self.box = box
        self.rotate = rotate
        self.pieces: list[Piece] = []
        self.fonts: dict[int, dict[str, Font]] = {}
        self.state = _State()
        self.stack: list[_State] = []
        self.tm: Matrix = IDENTITY
        self.tlm: Matrix = IDENTITY
        self.started: tuple[float, float, float] | None = None
        self.text: list[str] = []
        self.pen = 0.0

    def run(self, content: bytes, resources: object, ctm: Matrix, depth: int) -> None:
        if depth > MOST_FORMS:
            return
        lexer = Lexer(content)
        operands: list[object] = []
        self.state.ctm = ctm
        while (item := lexer.read()) is not END:
            if not isinstance(item, Keyword):
                operands.append(item)
                del operands[:-32]  # an operator takes at most a handful
                continue
            word = str(item)
            if word == "BI":
                _skip_inline_image(lexer)
            else:
                self._operator(word, operands, resources, depth, lexer)
            operands = []
        self._flush()

    def _operator(
        self, word: str, operands: list[object], resources: object, depth: int, lexer: Lexer
    ) -> None:
        numbers = [float(value) for value in operands if isinstance(value, int | float)]
        if word == "q":
            self.stack.append(self.state.copy())
        elif word == "Q":
            if self.stack:
                self.state = self.stack.pop()
        elif word == "cm" and len(numbers) >= 6:
            self.state.ctm = _multiply(_matrix(numbers[-6:]), self.state.ctm)
        elif word == "BT":
            self.tm = self.tlm = IDENTITY
        elif word == "ET":
            self._flush()
        elif word == "Tf" and len(operands) >= 2:
            self.state.size = numbers[-1] if numbers else 0.0
            name = next((value for value in operands if isinstance(value, Name)), None)
            self.state.font = self._font(resources, str(name) if name else "")
        elif word == "Td" and len(numbers) >= 2:
            self.tm = self.tlm = _multiply(_shift(numbers[-2], numbers[-1]), self.tlm)
        elif word == "TD" and len(numbers) >= 2:
            self.state.leading = -numbers[-1]
            self.tm = self.tlm = _multiply(_shift(numbers[-2], numbers[-1]), self.tlm)
        elif word == "Tm" and len(numbers) >= 6:
            self.tm = self.tlm = _matrix(numbers[-6:])
        elif word == "T*":
            self._next_line()
        elif word == "TL" and numbers:
            self.state.leading = numbers[-1]
        elif word == "Tc" and numbers:
            self.state.character_spacing = numbers[-1]
        elif word == "Tw" and numbers:
            self.state.word_spacing = numbers[-1]
        elif word == "Tz" and numbers:
            self.state.horizontal = numbers[-1] / 100
        elif word == "Ts" and numbers:
            self.state.rise = numbers[-1]
        elif word == "Tj" and operands:
            self._show(operands[-1])
        elif word == "'" and operands:
            self._next_line()
            self._show(operands[-1])
        elif word == '"' and len(operands) >= 3:
            self.state.word_spacing = numbers[0] if numbers else self.state.word_spacing
            self.state.character_spacing = numbers[1] if len(numbers) > 1 else 0.0
            self._next_line()
            self._show(operands[-1])
        elif word == "TJ" and operands:
            self._show_array(operands[-1])
        elif word == "Do" and operands:
            self._form(operands[-1], resources, depth)

    def _next_line(self) -> None:
        self.tm = self.tlm = _multiply(_shift(0.0, -self.state.leading), self.tlm)

    def _font(self, resources: object, name: str) -> Font | None:
        fonts = self.document.entry(resources, "Font", {})
        if not isinstance(fonts, dict):
            return None
        key = id(fonts)
        loaded = self.fonts.setdefault(key, {})
        if name not in loaded:
            loaded[name] = load_font(self.document, fonts.get(name), name)
        return loaded[name]

    def _form(self, name: object, resources: object, depth: int) -> None:
        if not isinstance(name, Name):
            return
        xobjects = self.document.entry(resources, "XObject", {})
        form = self.document.entry(xobjects, str(name))
        if not isinstance(form, Stream) or self.document.entry(form, "Subtype") != "Form":
            return
        matrix = self.document.entry(form, "Matrix")
        inner = IDENTITY
        if isinstance(matrix, list) and len(matrix) == 6:
            values = [self.document.resolve(value) for value in matrix]
            if all(isinstance(value, int | float) for value in values):
                inner = _matrix([float(value) for value in values])  # type: ignore[arg-type]
        try:
            content = self.document.data(form)
        except PdfError:
            return
        self.stack.append(self.state.copy())
        saved_tm, saved_tlm = self.tm, self.tlm
        self.run(
            content,
            self.document.entry(form, "Resources", resources),
            _multiply(inner, self.state.ctm),
            depth + 1,
        )
        self.tm, self.tlm = saved_tm, saved_tlm
        if self.stack:
            self.state = self.stack.pop()

    def _show_array(self, items: object) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, bytes):
                self._show(item)
            elif isinstance(item, int | float):
                shift = -float(item) / 1000 * self.state.size * self.state.horizontal
                self.tm = _multiply(_shift(shift, 0.0), self.tm)

    def _show(self, codes: object) -> None:
        if not isinstance(codes, bytes) or self.state.font is None:
            return
        font, state = self.state.font, self.state
        for code, character in font.read(codes):
            matrix = _multiply(self.tm, state.ctm)
            x, y = _place(
                matrix[4] + state.rise * matrix[2],
                matrix[5] + state.rise * matrix[3],
                self.box,
                self.rotate,
            )
            size = state.size * math.hypot(matrix[2], matrix[3])
            forward = (
                font.width(code) / 1000 * state.size
                + state.character_spacing
                + (state.word_spacing if code == 32 and not font.two_byte else 0.0)
            ) * state.horizontal
            width = abs(forward) * math.hypot(matrix[0], matrix[1])
            self._add(x, y, width, size, character)
            self.tm = _multiply(_shift(forward, 0.0), self.tm)

    def _add(self, x: float, y: float, width: float, size: float, character: str) -> None:
        if self.started is not None:
            _, at_y, at_size = self.started
            gap = x - self.pen
            same_line = abs(y - at_y) <= max(0.2, at_size * 0.1)
            if not same_line or gap > max(SPLIT_GAP * max(at_size, size), 0.5) or gap < -at_size:
                self._flush()
        if self.started is None:
            self.started = (x, y, size)
        self.text.append(character)
        self.pen = x + width

    def _flush(self) -> None:
        if self.started is None:
            return
        x, y, size = self.started
        text = "".join(self.text)
        self.started, self.text = None, []
        if text.strip():
            self.pieces.append(
                Piece(
                    page=self.number,
                    x=round(x, 2),
                    y=round(y, 2),
                    width=round(max(self.pen - x, 0.0), 2),
                    size=round(size, 2),
                    text=text.strip(),
                )
            )


def _matrix(values: list[float]) -> Matrix:
    a, b, c, d, e, f = values
    return (a, b, c, d, e, f)


def _shift(x: float, y: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, x, y)


def _multiply(first: Matrix, second: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = second
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _place(
    x: float, y: float, box: tuple[float, float, float, float], rotate: int
) -> tuple[float, float]:
    """A point in the page's own space, as points from the top left of the printed page."""
    x0, y0, x1, y1 = box
    across, up = x - x0, y - y0
    width, height = x1 - x0, y1 - y0
    if rotate == 90:
        return up, across
    if rotate == 180:
        return width - across, up
    if rotate == 270:
        return height - up, width - across
    return across, height - up


_INLINE_END = re.compile(rb"(?:^|[\x00\t\n\x0c\r ])EI(?=[\x00\t\n\x0c\r ]|$)")


def _skip_inline_image(lexer: Lexer) -> None:
    """Step over ``BI ... ID <bytes> EI``, whose bytes are a picture, not operators."""
    start = lexer.data.find(b"ID", lexer.at)
    if start < 0:
        lexer.at = len(lexer.data)
        return
    found = _INLINE_END.search(lexer.data, start + 2)
    lexer.at = found.end() if found else len(lexer.data)
