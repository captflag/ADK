"""The PDF file itself: its objects, its streams, and the pages they make."""

from __future__ import annotations

import base64
import re
import zlib

import pytest

from batchward.documents.pdf import (
    Document,
    Lexer,
    Name,
    PdfError,
    Ref,
    Stream,
    decode,
    read,
    read_file,
)
from pdf_documents import Printed, write_pdf

PAGE = [Printed(40.0, 50.0, "Ceiling Price (Rs.)")]


def parse(text: str) -> object:
    return Lexer(text.encode("latin-1")).read()


def test_reads_the_objects_a_pdf_holds():
    document = read(write_pdf([PAGE]))
    (page,) = document.pages()
    assert document.entry(page, "MediaBox") == [0, 0, 595, 842]
    assert not document.encrypted
    content = document.data(document.resolve(page["Contents"]))
    assert b"Ceiling Price" in content


def test_reads_a_compressed_content_stream():
    document = read(write_pdf([PAGE], compress=True))
    (page,) = document.pages()
    assert b"Ceiling Price" in document.data(document.resolve(page["Contents"]))


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("/Type", Name("Type")),
        ("/A#20B", Name("A B")),
        ("(one (two) three)", b"one (two) three"),
        (r"(a\053b\nc\\d)", b"a+b\nc\\d"),
        ("<48656C6C6F>", b"Hello"),
        ("<48656C6C6F7>", b"Hello\x70"),
        ("12 0 R", Ref(12, 0)),
        ("[1 2.5 /Three]", [1, 2.5, Name("Three")]),
        ("-3.25", -3.25),
        ("true", True),
        ("null", None),
        ("% a comment\n7", 7),
    ],
)
def test_reads_every_kind_of_object(written, expected):
    assert parse(written) == expected


def test_a_dictionary_keeps_its_entries_and_its_references():
    value = parse("<< /Type /Page /Count 3 /Parent 4 0 R >>")
    assert value == {"Type": "Page", "Count": 3, "Parent": Ref(4, 0)}
    assert isinstance(value["Type"], Name)


def test_two_numbers_are_not_a_reference():
    assert parse("[12 0]") == [12, 0]


def test_a_later_definition_of_an_object_wins():
    data = write_pdf([PAGE])
    added = data + b"\n1 0 obj\n<< /Replaced true >>\nendobj\n"
    assert read(added).objects[1] == {"Replaced": True}


def test_objects_kept_inside_an_object_stream_are_read():
    inner = b"<< /Type /Catalog /Pages 3 0 R >> << /Type /Pages /Kids [4 0 R] /Count 1 >>"
    offsets = b"2 0 3 40 "
    body = offsets + inner.replace(b">> <<", b">>" + b" " * (40 - len(offsets) - 34) + b"<<")
    packed = zlib.compress(body)
    document = read(
        b"%PDF-1.5\n"
        b"1 0 obj << /Type /ObjStm /N 2 /First "
        + str(len(offsets)).encode()
        + b" /Length "
        + str(len(packed)).encode()
        + b" /Filter /FlateDecode >>\nstream\n"
        + packed
        + b"\nendstream endobj\n"
        b"4 0 obj << /Type /Page /MediaBox [0 0 200 200] >> endobj\n"
        b"trailer << /Root 2 0 R >>\n%%EOF\n"
    )
    assert document.entry(document.objects[2], "Type") == "Catalog"
    (page,) = document.pages()
    assert document.entry(page, "MediaBox") == [0, 0, 200, 200]


def test_pages_are_found_even_when_the_catalog_is_lost():
    data = write_pdf([PAGE]).replace(b"/Type /Catalog", b"/Type /Broken")
    assert len(read(data).pages()) == 1


def test_a_page_inherits_what_the_tree_above_it_says():
    document = Document(
        objects={
            1: {"Type": "Pages", "Kids": [Ref(2)], "MediaBox": [0, 0, 300, 400], "Rotate": 90},
            2: {"Type": "Page", "Contents": Ref(3)},
            3: {"Type": "Catalog", "Pages": Ref(1)},
        },
        trailers=[{"Root": Ref(3)}],
    )
    (page,) = document.pages()
    assert (page["MediaBox"], page["Rotate"]) == ([0, 0, 300, 400], 90)


def test_a_page_tree_that_points_at_itself_does_not_go_round_for_ever():
    document = Document(
        objects={
            1: {"Type": "Pages", "Kids": [Ref(1), Ref(2)]},
            2: {"Type": "Page"},
        },
        trailers=[{"Root": {"Pages": Ref(1)}}],
    )
    assert len(document.pages()) == 1


def test_a_reference_that_points_at_itself_resolves_to_nothing():
    document = Document(objects={1: Ref(1)})
    assert document.resolve(Ref(1)) is None


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b"", "the file is empty"),
        (b"not a pdf at all", "it is not a PDF"),
        (b"%PDF-1.7\nnothing here\n", "holds no objects"),
    ],
)
def test_a_file_that_is_not_a_pdf_says_so(data, problem):
    with pytest.raises(PdfError, match=problem):
        read(data)


def test_a_file_that_asks_for_a_password_is_known_to_be_encrypted():
    data = write_pdf([PAGE]).replace(b"/Size", b"/Encrypt 99 0 R /Size")
    assert read(data).encrypted


def test_a_missing_file_says_which_one(tmp_path):
    with pytest.raises(PdfError, match=re.escape("cannot read gone.pdf")):
        read_file(tmp_path / "gone.pdf")


def stream(data: bytes, **attributes: object) -> tuple[Document, Stream]:
    return Document(), Stream(dict(attributes), data)


@pytest.mark.parametrize(
    ("raw", "filter_name", "expected"),
    [
        (b"48656C6C6F>", "ASCIIHexDecode", b"Hello"),
        (base64.a85encode(b"Hello") + b"~>", "ASCII85Decode", b"Hello"),
        (b"\x02abc" + bytes([254]) + b"z\x80", "RunLengthDecode", b"abczzz"),
        (zlib.compress(b"Hello"), "FlateDecode", b"Hello"),
    ],
)
def test_undoes_the_filters_a_published_pdf_uses(raw, filter_name, expected):
    document, holder = stream(raw, Filter=Name(filter_name))
    assert decode(document, holder) == expected


def test_undoes_the_lzw_the_pdf_standard_gives_an_example_of():
    # ISO 32000-1, 7.4.4.2: this encoding stands for 45 45 45 45 45 65 45 45 45 66.
    encoded = bytes([0x80, 0x0B, 0x60, 0x50, 0x22, 0x0C, 0x0C, 0x85, 0x01])
    document, holder = stream(encoded, Filter=Name("LZWDecode"))
    assert decode(document, holder) == bytes([45, 45, 45, 45, 45, 65, 45, 45, 45, 66])


def test_undoes_filters_one_after_another():
    packed = base64.a85encode(zlib.compress(b"Hello")) + b"~>"
    document, holder = stream(packed, Filter=[Name("ASCII85Decode"), Name("FlateDecode")])
    assert decode(document, holder) == b"Hello"


def test_undoes_the_png_prediction_a_cross_reference_stream_uses():
    rows = bytes([2, 1, 1, 1]) + bytes([2, 1, 1, 1])  # each row is the one above plus one
    document, holder = stream(
        zlib.compress(rows),
        Filter=Name("FlateDecode"),
        DecodeParms={"Predictor": 12, "Columns": 3},
    )
    assert decode(document, holder) == bytes([1, 1, 1, 2, 2, 2])


def test_a_stream_that_is_a_picture_is_not_read_as_text():
    document, holder = stream(b"\xff\xd8\xff", Filter=Name("DCTDecode"))
    with pytest.raises(PdfError, match="a picture, not text"):
        decode(document, holder)


def test_a_stream_whose_length_is_wrong_is_still_read():
    data = re.sub(rb"/Length \d+", b"/Length 999999", write_pdf([PAGE]), count=1)
    document = read(data)
    (page,) = document.pages()
    assert b"Ceiling Price" in document.data(document.resolve(page["Contents"]))
