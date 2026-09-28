"""A printed table read back out of a PDF page, as the rows and columns it shows.

A PDF holds no table: it holds text at points on a page, and a reader's eye
does the rest. This does the same work in code. Text sharing a baseline is a
line. The line that carries the column titles is found by name, which is what
keeps a gazette's preamble, its page numbers and its footnotes out of the
table, and the titles' own places fix where each column sits. Every line below
is cut at those columns.

A row of a real notification runs on for two or three lines: a long formulation
name or a manufacturer's address wraps inside its cell. A line that begins a
new row fills the first column; a line that does not, and that follows close
under the line above, is the rest of the row above. That rule is the whole
difference between 80 rows and 200 half-rows, and it is why the table is shown
as CSV for a person to check before anything is recorded.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from batchward.documents.pdf import Document, PdfError
from batchward.documents.pdf import read as read_pdf
from batchward.documents.text import Page, Piece, Reading, read_text

Titles = Sequence[str] | Mapping[str, str]
"""Column titles as a document prints them, or every spelling by the column it means."""

LINE_TOLERANCE = 0.35
"""How far above or below a baseline, in type sizes, still counts as the same line."""
SPACE_GAP = 0.18
"""A gap of this many type sizes between two pieces of text is a space between words."""
COLUMN_EDGE = 1.0
"""How far left of a column's edge, in points, text may start and still be that column's."""
ROW_GAP = 2.2
"""A line further than this many type sizes below the last belongs to no row above it."""
LEAST_GUTTER = 3
"""How many points wide a lane between two columns must be to count as one."""
TITLE_GAP = 1.2
"""A gap of this many type sizes between two words of a heading makes them two titles."""
LEAST_TITLES = 2
"""How many column titles a line must carry to be the table's heading."""
MOST_HEADING_LINES = 3
"""A heading is read over this many lines at most: ``Name of the Scheduled Formulation``
set in a narrow column takes three. The fewest lines that name the most columns wins,
so a heading of one line never swallows the row under it."""


class TableError(ValueError):
    """A document whose table cannot be found or read, saying what was looked for."""


@dataclass(frozen=True, slots=True)
class Line:
    """Text sharing a baseline, in reading order."""

    page: int
    y: float
    pieces: tuple[Piece, ...]

    @property
    def size(self) -> float:
        return max((piece.size for piece in self.pieces), default=0.0)

    @property
    def left(self) -> float:
        return min((piece.x for piece in self.pieces), default=0.0)

    @property
    def right(self) -> float:
        return max((piece.right for piece in self.pieces), default=0.0)

    @property
    def text(self) -> str:
        return join(self.pieces)


@dataclass(frozen=True, slots=True)
class Column:
    """Where one column of the table sits across the page."""

    title: str
    left: float
    right: float

    def holds(self, piece: Piece) -> bool:
        return self.left - COLUMN_EDGE <= piece.x < self.right - COLUMN_EDGE


@dataclass(frozen=True, slots=True)
class Table:
    """A table read from a document: its titles, its rows, and where they came from."""

    columns: tuple[Column, ...]
    rows: tuple[tuple[str, ...], ...]
    pages: tuple[int, ...]
    unreadable: int = 0
    """How many characters no font in the document could account for."""

    @property
    def titles(self) -> tuple[str, ...]:
        return tuple(column.title for column in self.columns)

    def as_csv(self) -> str:
        """The table as CSV, the heading first: what the importers read."""
        out = io.StringIO(newline="")
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(self.titles)
        writer.writerows(self.rows)
        return out.getvalue()

    def lines(self) -> list[str]:
        """The CSV as lines, for a reader that takes an open file."""
        return self.as_csv().splitlines(keepends=True)


def normal(text: str) -> str:
    """A column title with everything but its letters taken out, as the readers compare them."""
    return re.sub(r"[^a-z]", "", text.lower())


def same_line(one: Piece, other: Piece) -> bool:
    """Two pieces printed on one baseline, allowing for what sits a little above it."""
    return abs(one.y - other.y) <= max(1.0, LINE_TOLERANCE * max(one.size, other.size))


def in_reading_order(pieces: Iterable[Piece]) -> list[list[Piece]]:
    """Pieces gathered into the lines they sit on, top to bottom and left to right."""
    rows: list[list[Piece]] = []
    for piece in sorted(pieces, key=lambda piece: (piece.y, piece.x)):
        if rows and same_line(piece, rows[-1][0]):
            rows[-1].append(piece)
        else:
            rows.append([piece])
    return [sorted(row, key=lambda piece: piece.x) for row in rows]


def join(pieces: Iterable[Piece]) -> str:
    """Pieces of text in one cell, with a space wherever the printing left one."""
    text = ""
    last: Piece | None = None
    for row in in_reading_order(pieces):
        for piece in row:
            if last is not None:
                gap = piece.x - last.right
                size = max(piece.size, last.size, 1.0)
                if not same_line(piece, last) or gap > SPACE_GAP * size:
                    text += " "
            text += piece.text
            last = piece
    return " ".join(text.split())


def lines_of(page: Page) -> list[Line]:
    """A page's text as lines, top to bottom."""
    grouped: list[list[Piece]] = []
    for piece in sorted(page.pieces, key=lambda piece: (piece.y, piece.x)):
        if grouped:
            above = grouped[-1]
            baseline = above[0].y
            tolerance = max(1.0, LINE_TOLERANCE * max(piece.size, above[0].size))
            if abs(piece.y - baseline) <= tolerance:
                above.append(piece)
                continue
        grouped.append([piece])
    return [
        Line(page=page.number, y=pieces[0].y, pieces=tuple(sorted(pieces, key=lambda p: p.x)))
        for pieces in grouped
    ]


def find_table(reading: Reading, titles: Titles, *, least: int = LEAST_TITLES) -> Table:
    """The table whose heading carries these column titles, and every row under it.

    ``titles`` are the titles as the document prints them. A document that
    spells a column in more than one way gives a mapping of every spelling to
    the one column it means, which is how the importers already hold them. A
    heading that wraps over two or three lines is read as one heading.
    """
    wanted = _wanted(titles)
    if not wanted:
        raise TableError("no column titles were given to look for")
    pages = [page for page in reading.pages if not page.is_scan]
    if not pages:
        where = "the document" if len(reading.pages) == 1 else "every page of the document"
        raise TableError(f"{where} carries no text: it is a scan, and is not read here")
    by_page = {page.number: lines_of(page) for page in pages}
    heading = _heading(by_page, wanted, least)
    if heading is None:
        looked = ", ".join(sorted(set(wanted.values())))
        raise TableError(f"no heading in the document names {least} of these columns: {looked}")
    page_number, at, cells = heading
    body = _below(by_page, page_number, at)
    gutters = _gutters(body)
    columns = _widen(_columns(_split(cells, gutters)), gutters)
    rows, used = _rows(by_page, page_number, at, columns)
    return Table(
        columns=tuple(columns),
        rows=tuple(rows),
        pages=tuple(used),
        unreadable=sum(reading.page(number).unreadable for number in used),
    )


def read_table(document: Document, titles: Titles, *, least: int = LEAST_TITLES) -> Table:
    """The table a PDF prints, whose heading carries these column titles."""
    return find_table(read_text(document), titles, least=least)


def _wanted(titles: Titles) -> dict[str, str]:
    """Every spelling of a column title, by the column it means."""
    if isinstance(titles, Mapping):
        return {normal(title): str(key) for title, key in titles.items() if normal(title)}
    return {normal(title): title for title in titles if normal(title)}


def _heading(
    by_page: dict[int, list[Line]], wanted: dict[str, str], least: int
) -> tuple[int, int, list[list[Piece]]] | None:
    """The line that carries the column titles, gathered into the titles they spell."""
    columns = len(set(wanted.values()))
    best: tuple[int, int, int, list[list[Piece]]] | None = None
    for number, lines in sorted(by_page.items()):
        for at in range(len(lines)):
            for spread in range(1, MOST_HEADING_LINES + 1):
                if at + spread > len(lines):
                    continue
                taken = lines[at : at + spread]
                if not all(_close(above, below) for above, below in pairwise(taken)):
                    continue
                cells = _cells(taken)
                spelled = [normal(join(cell)) for cell in cells]
                found = len({wanted[text] for text in spelled if text in wanted})
                if found < least or found < columns / 2:
                    continue
                if best is None or found > best[0]:
                    best = (found, number, at + spread - 1, cells)
                if found == columns:
                    return best[1], best[2], best[3]
    if best is None:
        return None
    return best[1], best[2], best[3]


def _close(above: Line, below: Line) -> bool:
    return below.y - above.y <= ROW_GAP * max(above.size, below.size, 1.0)


def _cells(lines: Sequence[Line]) -> list[list[Piece]]:
    """A heading's own cells: pieces gathered into the titles they spell, left to right."""
    pieces = sorted(
        (piece for line in lines for piece in line.pieces), key=lambda piece: (piece.x, piece.y)
    )
    cells: list[list[Piece]] = []
    for piece in pieces:
        if cells:
            last = cells[-1]
            edge = max(held.right for held in last)
            start = min(held.x for held in last)
            gap = piece.x - edge
            size = max(piece.size, max(held.size for held in last), 1.0)
            if gap <= TITLE_GAP * size or piece.x < edge - 0.5 or abs(piece.x - start) < 0.5:
                last.append(piece)
                continue
        cells.append([piece])
    return cells


def _split(
    cells: Sequence[list[Piece]], gutters: Sequence[tuple[float, float]]
) -> list[list[Piece]]:
    """Two titles printed close together, told apart by the white lanes the rows leave.

    Where a heading reads ``S. No.  Name of Drugs`` with little between them,
    the rows below show that two columns start there, not one.
    """
    if not gutters:
        return list(cells)
    split: list[list[Piece]] = []
    for cell in cells:
        left = min(piece.x for piece in cell)
        right = max(piece.right for piece in cell)
        edges = sorted(
            middle for low, high in gutters if left < (middle := (low + high) / 2) < right
        )
        if not edges:
            split.append(cell)
            continue
        for edge in [left, *edges]:
            beyond = next((other for other in edges if other > edge), float("inf"))
            part = [piece for piece in cell if edge <= piece.x < beyond]
            if part:
                split.append(part)
    return split


def _columns(cells: Sequence[list[Piece]]) -> list[Column]:
    """Each column runs from its own title to the next title's, the first from the margin."""
    starts = [min(piece.x for piece in cell) for cell in cells]
    columns = []
    for index, cell in enumerate(cells):
        right = starts[index + 1] if index + 1 < len(cells) else float("inf")
        columns.append(
            Column(title=join(cell), left=0.0 if index == 0 else starts[index], right=right)
        )
    return columns


def _below(by_page: dict[int, list[Line]], page_number: int, at: int) -> list[Line]:
    """Every line under the heading, on its page and the pages after it."""
    lines: list[Line] = []
    for number in sorted(page for page in by_page if page >= page_number):
        lines += by_page[number][at + 1 :] if number == page_number else by_page[number]
    return lines


def _gutters(lines: Sequence[Line]) -> list[tuple[float, float]]:
    """The white lanes down the page that the rows leave between their columns.

    A lane counts as white when nearly every line leaves it empty, so one note
    printed across the whole width does not close the table's columns.
    """
    spans = [(piece.x, piece.right) for line in lines for piece in line.pieces]
    if not spans:
        return []
    start, end = int(min(left for left, _ in spans)), int(max(right for _, right in spans)) + 2
    counts = [0] * (end - start + 1)
    for line in lines:
        covered: set[int] = set()
        for piece in line.pieces:
            covered.update(range(int(piece.x) - start, int(piece.right) - start + 1))
        for at in covered:
            if 0 <= at < len(counts):
                counts[at] += 1
    allowed = max(1, round(len(lines) * 0.1))
    gutters: list[tuple[float, float]] = []
    lane: int | None = None
    for at, count in enumerate(counts):
        if count < allowed:
            lane = at if lane is None else lane
            continue
        if lane is not None and at - lane >= LEAST_GUTTER:
            gutters.append((float(lane + start), float(at + start)))
        lane = None
    if lane is not None and len(counts) - lane >= LEAST_GUTTER:
        gutters.append((float(lane + start), float(len(counts) + start)))
    return gutters


def _widen(columns: list[Column], gutters: Sequence[tuple[float, float]]) -> list[Column]:
    """Each column's edge moved onto the white lane beside it, where there is one.

    A title is often printed centred over its column, or the figures under it
    are set to the right, so where a title starts is near its column's edge but
    not on it. The rows themselves show where the edge really is.
    """
    if len(columns) < 2 or not gutters:
        return columns
    edges = [column.left for column in columns] + [float("inf")]
    moved = list(edges)
    for index in range(1, len(columns)):
        edge = edges[index]
        if any(low <= edge <= high for low, high in gutters):
            continue
        near = [
            (low + high) / 2
            for low, high in gutters
            if edges[index - 1] < (low + high) / 2 < edges[index + 1]
        ]
        if near:
            moved[index] = round(min(near, key=lambda middle: abs(middle - edge)), 2)
    for index in range(1, len(moved)):
        moved[index] = max(moved[index], moved[index - 1])
    return [
        Column(title=column.title, left=moved[index], right=moved[index + 1])
        for index, column in enumerate(columns)
    ]


def _rows(
    by_page: dict[int, list[Line]], page_number: int, at: int, columns: list[Column]
) -> tuple[list[tuple[str, ...]], list[int]]:
    """Every line below the heading, cut at the columns, with wrapped lines joined on."""
    titles = {normal(column.title) for column in columns if normal(column.title)}
    rows: list[list[str]] = []
    used: list[int] = []
    previous: Line | None = None
    for number in sorted(page for page in by_page if page >= page_number):
        lines = by_page[number][at + 1 :] if number == page_number else by_page[number]
        for line in lines:
            cells = _cut(line, columns)
            filled = [cell for cell in cells if cell.strip()]
            if not filled:
                continue
            carries_on = (
                bool(rows)
                and not cells[0].strip()
                and previous is not None
                and previous.page == line.page
                and _close(previous, line)
            )
            if carries_on:
                rows[-1] = [
                    (held + " " + cell).strip() if cell else held
                    for held, cell in zip(rows[-1], cells, strict=True)
                ]
            elif cells[0].strip() or len(filled) > 1:
                rows.append(list(cells))
            else:  # a page number or a footnote standing alone under the table
                previous = None
                continue
            previous = line
            if number not in used:
                used.append(number)
    kept = [tuple(row) for row in rows if not _is_heading_again(row, titles)]
    return kept, used


def _cut(line: Line, columns: Sequence[Column]) -> list[str]:
    """One line's text, column by column."""
    held: list[list[Piece]] = [[] for _ in columns]
    for piece in line.pieces:
        for index, column in enumerate(columns):
            if column.holds(piece):
                held[index].append(piece)
                break
    return [join(pieces) for pieces in held]


def _is_heading_again(cells: Sequence[str], titles: set[str]) -> bool:
    """A heading repeated at the top of the next page is not a row."""
    filled = [cell for cell in cells if cell.strip()]
    return len(filled) >= 2 and all(normal(cell) in titles for cell in filled)


def table_from_pdf(data: bytes, titles: Titles, *, least: int = LEAST_TITLES) -> Table:
    """The table a PDF's bytes print, whose heading carries these column titles."""
    try:
        return read_table(read_pdf(data), titles, least=least)
    except PdfError as error:
        raise TableError(str(error)) from error
