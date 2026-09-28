"""Small PDFs written by hand, so that reading them back can be checked.

The reader under test is given documents built the way real ones are built: a
content stream of text placed by a matrix, in a font that says what its codes
mean. The builder writes the plain form, the compressed form, a font whose
codes are its own, and a heading printed over two lines above rows that wrap —
all of which a published notification does.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence
from dataclasses import dataclass

A4 = (595.0, 842.0)


@dataclass(frozen=True, slots=True)
class Printed:
    """One piece of text placed on a page, measured from the top left."""

    x: float
    y: float
    text: str
    size: float = 9.0


def escape(text: str) -> bytes:
    out = text.encode("cp1252", errors="replace")
    for character, replacement in ((b"\\", b"\\\\"), (b"(", b"\\("), (b")", b"\\)")):
        out = out.replace(character, replacement)
    return out


def write_pdf(
    pages: Sequence[Sequence[Printed]],
    *,
    size: tuple[float, float] = A4,
    compress: bool = False,
    font: str = "simple",
    widths: int | None = 500,
    rotate: int = 0,
) -> bytes:
    """A PDF printing these pieces of text, one page at a time."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    characters = sorted({character for page in pages for piece in page for character in piece.text})
    codes = {character: index + 1 for index, character in enumerate(characters)}
    font_number = _font_object(add, font, widths, codes)

    page_numbers = []
    for pieces in pages:
        content = _content(pieces, size, font, codes, rotate)
        raw = zlib.compress(content) if compress else content
        extra = b" /Filter /FlateDecode" if compress else b""
        head = b"<< /Length " + str(len(raw)).encode() + extra + b" >>"
        stream = add(head + b"\nstream\n" + raw + b"\nendstream")
        page_numbers.append((stream,))
    kids = []
    for (stream_number,) in page_numbers:
        page = add(
            b"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 "
            + f"{size[0]:g} {size[1]:g}".encode()
            + b"]"
            + (f" /Rotate {rotate}".encode() if rotate else b"")
            + b" /Resources << /Font << /F1 "
            + str(font_number).encode()
            + b" 0 R >> >> /Contents "
            + str(stream_number).encode()
            + b" 0 R >>"
        )
        kids.append(page)
    tree = add(
        b"<< /Type /Pages /Count "
        + str(len(kids)).encode()
        + b" /Kids ["
        + b" ".join(f"{number} 0 R".encode() for number in kids)
        + b"] >>"
    )
    catalog = add(b"<< /Type /Catalog /Pages " + str(tree).encode() + b" 0 R >>")
    objects = [
        body.replace(b"/Parent 0 0 R", b"/Parent " + str(tree).encode() + b" 0 R")
        for body in objects
    ]
    return _file(objects, catalog)


def write_raw(
    content: bytes,
    *,
    objects: Sequence[bytes] = (),
    resources: bytes | None = None,
    size: tuple[float, float] = A4,
) -> bytes:
    """A one-page PDF whose content stream is given as written, for odder constructs.

    Object 1 is a Helvetica font, so ``/F1 1 0 R`` names it; any ``objects``
    given follow it, numbered from 2.
    """
    bodies = [b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", *objects]
    numbers = len(bodies)
    head = b"<< /Length " + str(len(content)).encode() + b" >>"
    bodies.append(head + b"\nstream\n" + content + b"\nendstream")
    page = (
        b"<< /Type /Page /Parent "
        + str(numbers + 3).encode()
        + b" 0 R /MediaBox [0 0 "
        + f"{size[0]:g} {size[1]:g}".encode()
        + b"] /Resources "
        + (resources or b"<< /Font << /F1 1 0 R >> >>")
        + b" /Contents "
        + str(numbers + 1).encode()
        + b" 0 R >>"
    )
    bodies.append(page)
    bodies.append(b"<< /Type /Pages /Count 1 /Kids [" + str(numbers + 2).encode() + b" 0 R] >>")
    bodies.append(b"<< /Type /Catalog /Pages " + str(numbers + 3).encode() + b" 0 R >>")
    return _file(bodies, numbers + 4)


def _content(
    pieces: Sequence[Printed],
    size: tuple[float, float],
    font: str,
    codes: dict[str, int],
    rotate: int,
) -> bytes:
    out = bytearray()
    for piece in pieces:
        if font == "codes":
            shown = b"<" + b"".join(f"{codes[c]:04X}".encode() for c in piece.text) + b">"
        elif font == "differences":
            shown = b"<" + b"".join(f"{codes[c]:02X}".encode() for c in piece.text) + b">"
        else:
            shown = b"(" + escape(piece.text) + b")"
        if rotate == 90:
            # Turned a quarter turn in the page's own space, so that a reader
            # displaying the page turned the other way reads it across.
            placed = f"0 1 -1 0 {piece.y:.2f} {piece.x:.2f}".encode()
        else:
            placed = f"1 0 0 1 {piece.x:.2f} {size[1] - piece.y:.2f}".encode()
        out += (
            b"BT /F1 "
            + f"{piece.size:g}".encode()
            + b" Tf "
            + placed
            + b" Tm "
            + shown
            + b" Tj ET\n"
        )
    return bytes(out)


def _font_object(add, font: str, widths: int | None, codes: dict[str, int]) -> int:
    if font == "codes":
        return _cid_font(add, widths or 500, codes)
    body = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
    if widths is not None:
        body += (
            b" /FirstChar 32 /LastChar 126 /Widths ["
            + b" ".join([str(widths).encode()] * 95)
            + b"]"
        )
    if font == "differences":
        listed = b" ".join(f"/{_glyph(character)}".encode() for character in sorted(codes))
        body = body.replace(b"/FirstChar 32", b"/FirstChar 1")
        body = body.replace(b"/LastChar 126", b"/LastChar 95")
        body += b" /Encoding << /Type /Encoding /Differences [1 " + listed + b"] >>"
    else:
        body += b" /Encoding /WinAnsiEncoding"
    return add(body + b" >>")


def _glyph(character: str) -> str:
    names = {
        " ": "space",
        ".": "period",
        ",": "comma",
        "(": "parenleft",
        ")": "parenright",
        "/": "slash",
        "-": "hyphen",
        "&": "ampersand",
        "%": "percent",
        "+": "plus",
        ":": "colon",
        "#": "numbersign",
    }
    digits = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    if character.isdigit():
        return digits[int(character)]
    return names.get(character, character if character.isalpha() else f"uni{ord(character):04X}")


def _cid_font(add, width: int, codes: dict[str, int]) -> int:
    """A subset font whose codes are its own, with a table saying what they print."""
    pairs = b"".join(
        f"<{code:04X}> <{ord(character):04X}>\n".encode() for character, code in codes.items()
    )
    cmap = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        + str(len(codes)).encode()
        + b" beginbfchar\n"
        + pairs
        + b"endbfchar\nendcmap end end"
    )
    to_unicode = add(
        b"<< /Length " + str(len(cmap)).encode() + b" >>\nstream\n" + cmap + b"\nendstream"
    )
    descendant = add(
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /AAAAAA+Helvetica"
        b" /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >>"
        b" /DW " + str(width).encode() + b" >>"
    )
    return add(
        b"<< /Type /Font /Subtype /Type0 /BaseFont /AAAAAA+Helvetica /Encoding /Identity-H"
        b" /DescendantFonts [" + str(descendant).encode() + b" 0 R]"
        b" /ToUnicode " + str(to_unicode).encode() + b" 0 R >>"
    )


def _file(objects: Sequence[bytes], catalog: int) -> bytes:
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root "
        + str(catalog).encode()
        + b" 0 R >>\nstartxref\n"
        + str(start).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


def lay_out(
    columns: Sequence[float],
    heading: Sequence[Sequence[str]],
    rows: Sequence[Sequence[Sequence[str]]],
    *,
    top: float = 90.0,
    size: float = 9.0,
    leading: float = 11.0,
    between: float = 4.0,
    centre_last: bool = False,
) -> list[Printed]:
    """A table printed the way a notification prints one: a heading, then rows that wrap."""
    pieces: list[Printed] = []
    y = top
    for line in heading:
        for column, text in zip(columns, line, strict=False):
            if text:
                pieces.append(Printed(column, y, text, size))
        y += leading
    y += between
    for row in rows:
        height = max((len(cell) for cell in row), default=1)
        for index, cell in enumerate(row):
            for offset, text in enumerate(cell):
                if not text:
                    continue
                x = columns[index]
                if centre_last and index == len(columns) - 1:
                    x = columns[index] + 40 - len(text) * size * 0.25
                pieces.append(Printed(x, y + offset * leading, text, size))
        y += height * leading + between
    return pieces
