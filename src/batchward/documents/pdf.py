"""A PDF read as the objects it holds, in plain Python.

NPPA publishes its ceiling price notifications, and CDSCO its monthly drug
alert lists, as PDFs whose tables a stockist has to act on (ADR 0013,
ADR 0014). A PDF written by a word processor or a typesetter carries the text
it prints, each piece placed at a point on the page. That text layer is what is
read here: nothing is recognised from a picture, so a scanned page yields
nothing and is reported as a scan rather than guessed at.

This module is the file itself: its objects, the streams they hold and the
pages they make. ``batchward.documents.text`` works out where each piece of
text sits on the page, and ``batchward.documents.tables`` puts those pieces
back into rows and columns.

Objects are found by reading the file from front to back rather than by
following its cross-reference table, skipping over each stream's bytes as it
goes. Broken cross-reference tables are common in documents that have been
edited, and a table the office must check is worth reading anyway; where a
number is defined twice, the later definition wins, as an incremental update
means it to.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

MOST_PAGES = 5000
"""A page tree deeper or wider than any real notification is a loop or a lie."""


class PdfError(ValueError):
    """A PDF that cannot be read, saying what could not be read."""


class Name(str):
    """A PDF name, written ``/Type``. A plain ``bytes`` is a PDF string."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"/{str.__str__(self)}"


class Keyword(str):
    """A bare word: ``obj``, ``R``, or an operator in a content stream."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Ref:
    """A reference to another object, written ``12 0 R``."""

    number: int
    generation: int = 0


@dataclass(frozen=True, slots=True)
class Stream:
    """A dictionary with bytes attached, still in whatever filters encode it."""

    attributes: dict[str, object]
    raw: bytes


_SPACE = b"\x00\t\n\x0c\r "
_DELIMITER = b"()<>[]{}/%"
END = object()
"""Returned when there is nothing left to read."""


class Lexer:
    """PDF syntax, read one object at a time. Used for files and content streams."""

    __slots__ = ("at", "data")

    def __init__(self, data: bytes, at: int = 0) -> None:
        self.data = data
        self.at = at

    def skip_space(self) -> None:
        data = self.data
        while self.at < len(data):
            byte = data[self.at]
            if byte in _SPACE:
                self.at += 1
            elif byte == 0x25:  # a % comment runs to the end of the line
                ends = (data.find(b"\r", self.at), data.find(b"\n", self.at), len(data))
                self.at = min(end for end in ends if end >= 0)
            else:
                return

    def read(self) -> object:
        """The next object, or a ``Keyword``, or ``END`` at the end of the data."""
        self.skip_space()
        data = self.data
        if self.at >= len(data):
            return END
        byte = data[self.at]
        if byte == 0x2F:  # /
            return self._name()
        if byte == 0x28:  # (
            return self._literal_string()
        if byte == 0x3C:  # <
            if data[self.at : self.at + 2] == b"<<":
                return self._dictionary()
            return self._hex_string()
        if byte == 0x5B:  # [
            return self._array()
        if data[self.at : self.at + 2] == b">>":
            self.at += 2
            return Keyword(">>")
        if byte in b"]}":
            self.at += 1
            return Keyword(chr(byte))
        if byte == 0x7B:  # {
            self.at += 1
            return Keyword("{")
        return self._token()

    def _word(self) -> bytes:
        start = self.at
        data = self.data
        while self.at < len(data):
            byte = data[self.at]
            if byte in _SPACE or byte in _DELIMITER:
                break
            self.at += 1
        if self.at == start:  # a lone delimiter: step over it so reading cannot stall
            self.at += 1
        return data[start : self.at]

    def _token(self) -> object:
        word = self._word()
        if word == b"true":
            return True
        if word == b"false":
            return False
        if word == b"null":
            return None
        number = _number(word)
        if number is None:
            return Keyword(word.decode("latin-1"))
        if isinstance(number, int):
            return self._maybe_reference(number)
        return number

    def _maybe_reference(self, number: int) -> object:
        """``12 0 R`` is a reference; ``12 0`` is two numbers."""
        if number < 0:
            return number
        mark = self.at
        self.skip_space()
        start = self.at
        word = self._word()
        generation = _number(word)
        if isinstance(generation, int) and generation >= 0 and start != self.at:
            self.skip_space()
            if self._word() == b"R":
                return Ref(number, generation)
        self.at = mark
        return number

    def _name(self) -> Name:
        self.at += 1
        word = self._word()
        if b"#" in word:
            word = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m[1], 16)]), word)
        return Name(word.decode("latin-1"))

    def _literal_string(self) -> bytes:
        data = self.data
        self.at += 1
        out = bytearray()
        depth = 1
        while self.at < len(data):
            byte = data[self.at]
            self.at += 1
            if byte == 0x5C:  # backslash
                if self.at >= len(data):
                    break
                escaped = data[self.at]
                self.at += 1
                if escaped in b"01234567":
                    digits = chr(escaped)
                    while len(digits) < 3 and self.at < len(data) and data[self.at] in b"01234567":
                        digits += chr(data[self.at])
                        self.at += 1
                    out.append(int(digits, 8) & 0xFF)
                elif escaped == 0x0D:  # a line continuation, and \r\n counts once
                    if self.at < len(data) and data[self.at] == 0x0A:
                        self.at += 1
                elif escaped == 0x0A:
                    pass
                else:
                    named = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}
                    out.append(named.get(escaped, escaped))
            elif byte == 0x28:
                depth += 1
                out.append(byte)
            elif byte == 0x29:
                depth -= 1
                if depth == 0:
                    break
                out.append(byte)
            else:
                out.append(byte)
        return bytes(out)

    def _hex_string(self) -> bytes:
        end = self.data.find(b">", self.at)
        if end < 0:
            raise PdfError("a hex string is never closed")
        digits = re.sub(rb"[^0-9A-Fa-f]", b"", self.data[self.at + 1 : end])
        self.at = end + 1
        if len(digits) % 2:
            digits += b"0"
        return bytes.fromhex(digits.decode("ascii"))

    def _array(self) -> list[object]:
        self.at += 1
        items: list[object] = []
        while True:
            item = self.read()
            if item is END:
                raise PdfError("an array is never closed")
            if isinstance(item, Keyword) and item in ("]", ">>"):
                return items
            items.append(item)

    def _dictionary(self) -> dict[str, object]:
        self.at += 2
        pairs: dict[str, object] = {}
        while True:
            key = self.read()
            if key is END:
                raise PdfError("a dictionary is never closed")
            if isinstance(key, Keyword) and key in (">>", "]"):
                return pairs
            if not isinstance(key, Name):
                continue  # a stray token between entries: skip it rather than stop reading
            value = self.read()
            if value is END:
                raise PdfError("a dictionary is never closed")
            pairs[str(key)] = value


def _number(word: bytes) -> int | float | None:
    if not word or not re.fullmatch(rb"[+-]?(?:\d+\.?\d*|\.\d+|\.)", word):
        return None
    text = word.decode("ascii")
    if "." in text:
        try:
            return float(text.rstrip(".") or "0")
        except ValueError:  # pragma: no cover - the pattern above allows no other form
            return None
    return int(text)


_OBJECT = re.compile(rb"(?<![\d.])(\d{1,10})\s+(\d{1,5})\s+obj\b")


@dataclass(slots=True)
class Document:
    """Every object in a PDF file, and the pages they make."""

    objects: dict[int, object] = field(default_factory=dict)
    trailers: list[dict[str, object]] = field(default_factory=list)

    def resolve(self, value: object) -> object:
        """Follow references until something else is reached."""
        seen = set()
        while isinstance(value, Ref):
            if value.number in seen:
                return None
            seen.add(value.number)
            value = self.objects.get(value.number)
        return value

    def entry(self, holder: object, key: str, default: object = None) -> object:
        """One entry of a dictionary or stream, resolved."""
        holder = self.resolve(holder)
        if isinstance(holder, Stream):
            holder = holder.attributes
        if not isinstance(holder, dict):
            return default
        value = self.resolve(holder.get(key))
        return default if value is None else value

    def data(self, stream: object) -> bytes:
        """A stream's bytes with its filters undone."""
        stream = self.resolve(stream)
        if not isinstance(stream, Stream):
            raise PdfError("expected a stream")
        return decode(self, stream)

    def of_type(self, type_name: str) -> list[object]:
        """Every object with this ``/Type``, in object number order."""
        found = []
        for number in sorted(self.objects):
            value = self.objects[number]
            attributes = value.attributes if isinstance(value, Stream) else value
            if isinstance(attributes, dict) and attributes.get("Type") == type_name:
                found.append(value)
        return found

    @property
    def encrypted(self) -> bool:
        return any("Encrypt" in trailer for trailer in self.trailers)

    def pages(self) -> list[dict[str, object]]:
        """Every page, in printed order, each with what it inherits filled in."""
        roots = [self.entry(trailer, "Root") for trailer in self.trailers]
        catalogs = [root for root in roots if isinstance(root, dict)] or self.of_type("Catalog")
        for catalog in catalogs:
            tree = self.entry(catalog, "Pages")
            if isinstance(tree, dict):
                pages = self._walk(tree, {}, set())
                if pages:
                    return pages
        return [page for page in self.of_type("Page") if isinstance(page, dict)]

    def _walk(
        self, node: dict[str, object], inherited: dict[str, object], seen: set[int]
    ) -> list[dict[str, object]]:
        carried = dict(inherited)
        for key in ("Resources", "MediaBox", "CropBox", "Rotate"):
            if key in node:
                carried[key] = node[key]
        if node.get("Type") == "Page" or ("Kids" not in node and "Contents" in node):
            return [carried | node]
        kids = self.resolve(node.get("Kids"))
        if not isinstance(kids, list):
            return []
        pages: list[dict[str, object]] = []
        for kid in kids:
            if isinstance(kid, Ref):
                if kid.number in seen:
                    continue
                seen.add(kid.number)
            child = self.resolve(kid)
            if isinstance(child, dict):
                pages += self._walk(child, carried, seen)
            if len(pages) > MOST_PAGES:
                raise PdfError(f"the page tree holds more than {MOST_PAGES} pages")
        return pages


def read(data: bytes) -> Document:
    """Every object in a PDF file, read from front to back."""
    if not data:
        raise PdfError("the file is empty")
    start = data.find(b"%PDF-")
    if start < 0 or start > 1024:
        raise PdfError("the file does not start with %PDF-: it is not a PDF")
    document = Document()
    at = start
    while (found := _OBJECT.search(data, at)) is not None:
        number = int(found[1])
        lexer = Lexer(data, found.end())
        try:
            value = _object_at(lexer)
        except PdfError:
            at = found.end()
            continue
        document.objects[number] = value
        at = lexer.at
    for keyword in re.finditer(rb"\btrailer\b", data):
        trailer = Lexer(data, keyword.end()).read()
        if isinstance(trailer, dict):
            document.trailers.append(trailer)
    for stream in document.of_type("XRef"):
        document.trailers.append(stream.attributes)  # type: ignore[union-attr]
    _unpack_object_streams(document)
    if not document.objects:
        raise PdfError("the file holds no objects: it is not a PDF, or it is damaged")
    return document


def _object_at(lexer: Lexer) -> object:
    value = lexer.read()
    if value is END:
        raise PdfError("an object ends where it begins")
    mark = lexer.at
    following = lexer.read()
    attached = isinstance(following, Keyword) and following == "stream"
    if not attached or not isinstance(value, dict):
        lexer.at = mark
        return value
    data = lexer.data
    at = lexer.at
    if data[at : at + 2] == b"\r\n":
        at += 2
    elif data[at : at + 1] in (b"\n", b"\r"):
        at += 1
    length = value.get("Length")
    end = -1
    if (
        isinstance(length, int)
        and 0 <= length <= len(data) - at
        and re.match(rb"\s*endstream", data[at + length : at + length + 20])
    ):
        end = at + length
    if end < 0:
        end = data.find(b"endstream", at)
        if end < 0:
            raise PdfError("a stream is never closed")
        while end > at and data[end - 1] in b"\r\n":
            end -= 1
    lexer.at = data.find(b"endstream", end)
    lexer.at = len(data) if lexer.at < 0 else lexer.at + len(b"endstream")
    return Stream(value, data[at:end])


def _unpack_object_streams(document: Document) -> None:
    """Objects kept inside an object stream, which a file of PDF 1.5 or later may do.

    An object defined at the top of the file wins: an incremental update writes
    its new version there, while the object stream holds what it replaced.
    """
    for stream in document.of_type("ObjStm"):
        if not isinstance(stream, Stream):  # pragma: no cover - of_type only returns objects
            continue
        try:
            data = decode(document, stream)
        except PdfError:
            continue
        count = document.entry(stream, "N", 0)
        first = document.entry(stream, "First", 0)
        if not isinstance(count, int) or not isinstance(first, int):
            continue
        header = Lexer(data[:first])
        pairs = []
        for _ in range(count):
            number, offset = header.read(), header.read()
            if not isinstance(number, int) or not isinstance(offset, int):
                break
            pairs.append((number, offset))
        for number, offset in pairs:
            if number in document.objects:
                continue
            value = Lexer(data, first + offset).read()
            if value is not END:
                document.objects[number] = value


def read_file(path: Path) -> Document:
    """Every object in a PDF file on disk."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise PdfError(f"cannot read {path.name}: {error}") from error
    return read(data)


def decode(document: Document, stream: Stream) -> bytes:
    """A stream's bytes with every filter undone, in the order they are named."""
    data = stream.raw
    filters = document.resolve(stream.attributes.get("Filter"))
    parameters = document.resolve(stream.attributes.get("DecodeParms", stream.attributes.get("DP")))
    if filters is None:
        return data
    if isinstance(filters, Name):
        filters = [filters]
    if not isinstance(filters, list):
        raise PdfError(f"a stream names its filter as {filters!r}")
    if isinstance(parameters, dict) or parameters is None:
        parameters = [parameters] * len(filters)
    if not isinstance(parameters, list):
        parameters = [None] * len(filters)
    parameters += [None] * (len(filters) - len(parameters))
    for name, parameter in zip(filters, parameters, strict=False):
        data = _undo(document, str(name), data, document.resolve(parameter))
    return data


def _undo(document: Document, name: str, data: bytes, parameter: object) -> bytes:
    if name in ("FlateDecode", "Fl"):
        data = _inflate(data)
    elif name in ("LZWDecode", "LZW"):
        early = document.entry(parameter, "EarlyChange", 1)
        data = _unlzw(data, early != 0)
    elif name in ("ASCIIHexDecode", "AHx"):
        return _unhex(data)
    elif name in ("ASCII85Decode", "A85"):
        return _un85(data)
    elif name in ("RunLengthDecode", "RL"):
        return _unrun(data)
    elif name == "Crypt":
        return data
    else:
        raise PdfError(f"the stream is encoded with {name}, which is a picture, not text")
    if isinstance(parameter, dict):
        data = _unpredict(
            data,
            predictor=int(document.entry(parameter, "Predictor", 1) or 1),
            colours=int(document.entry(parameter, "Colors", 1) or 1),
            bits=int(document.entry(parameter, "BitsPerComponent", 8) or 8),
            columns=int(document.entry(parameter, "Columns", 1) or 1),
        )
    return data


def _inflate(data: bytes) -> bytes:
    for attempt in (data, data.lstrip(b"\r\n \t"), data[1:]):
        machine = zlib.decompressobj()
        try:
            out = machine.decompress(attempt)
        except zlib.error:
            continue
        if out:
            return out
    machine = zlib.decompressobj(-zlib.MAX_WBITS)  # written without a zlib header
    try:
        return machine.decompress(data)
    except zlib.error as error:
        raise PdfError(f"a compressed stream cannot be read: {error}") from error


def _unhex(data: bytes) -> bytes:
    digits = re.sub(rb"[^0-9A-Fa-f>]", b"", data).split(b">")[0]
    if len(digits) % 2:
        digits += b"0"
    return bytes.fromhex(digits.decode("ascii"))


def _un85(data: bytes) -> bytes:
    import base64

    text = re.sub(rb"\s", b"", data)
    if text.startswith(b"<~"):
        text = text[2:]
    text = text.split(b"~>")[0]
    try:
        return base64.a85decode(text)
    except ValueError as error:
        raise PdfError(f"an ASCII85 stream cannot be read: {error}") from error


def _unrun(data: bytes) -> bytes:
    out = bytearray()
    at = 0
    while at < len(data):
        length = data[at]
        at += 1
        if length == 128:
            break
        if length < 128:
            out += data[at : at + length + 1]
            at += length + 1
        elif at < len(data):
            out += bytes([data[at]]) * (257 - length)
            at += 1
    return bytes(out)


def _unlzw(data: bytes, early: bool) -> bytes:
    out = bytearray()
    table: list[bytes] = [bytes([i]) for i in range(256)] + [b"", b""]
    width, previous = 9, None
    buffer = bits = 0
    for byte in data:
        buffer = (buffer << 8) | byte
        bits += 8
        while bits >= width:
            bits -= width
            code = (buffer >> bits) & ((1 << width) - 1)
            buffer &= (1 << bits) - 1
            if code == 256:
                table = table[:258]
                width, previous = 9, None
                continue
            if code == 257:
                return bytes(out)
            if previous is None:
                entry = table[code]
            elif code < len(table):
                entry = table[code]
                table.append(previous + entry[:1])
            elif code == len(table):
                entry = previous + previous[:1]
                table.append(entry)
            else:
                raise PdfError("an LZW stream holds a code that was never made")
            out += entry
            previous = entry
            limit = len(table) + (1 if early else 0)
            width = 9 if limit < 512 else 10 if limit < 1024 else 11 if limit < 2048 else 12
    return bytes(out)


def _unpredict(data: bytes, *, predictor: int, colours: int, bits: int, columns: int) -> bytes:
    if predictor <= 1:
        return data
    step = max(1, (colours * bits + 7) // 8)
    row_length = max(1, (columns * colours * bits + 7) // 8)
    if predictor == 2:  # the TIFF predictor, which only differs for whole bytes
        if bits != 8:
            return data
        out = bytearray(data)
        for start in range(0, len(out) - row_length + 1, row_length):
            for at in range(start + step, start + row_length):
                out[at] = (out[at] + out[at - step]) & 0xFF
        return bytes(out)
    out = bytearray()
    previous = bytearray(row_length)
    at = 0
    while at + 1 <= len(data) - 1:
        kind = data[at]
        row = bytearray(data[at + 1 : at + 1 + row_length])
        row += bytes(row_length - len(row))
        at += 1 + row_length
        for index in range(row_length):
            left = row[index - step] if index >= step else 0
            above = previous[index]
            corner = previous[index - step] if index >= step else 0
            if kind == 1:
                row[index] = (row[index] + left) & 0xFF
            elif kind == 2:
                row[index] = (row[index] + above) & 0xFF
            elif kind == 3:
                row[index] = (row[index] + (left + above) // 2) & 0xFF
            elif kind == 4:
                guess = left + above - corner
                distances = (abs(guess - left), abs(guess - above), abs(guess - corner))
                nearest = (left, above, corner)[distances.index(min(distances))]
                row[index] = (row[index] + nearest) & 0xFF
        out += row
        previous = row
    return bytes(out)
