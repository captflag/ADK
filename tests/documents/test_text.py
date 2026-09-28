"""Where each piece of text sits on the page, and what it says."""

from __future__ import annotations

import pytest

from batchward.documents.glyphs import UNREADABLE
from batchward.documents.pdf import PdfError
from batchward.documents.pdf import read as read_pdf
from batchward.documents.text import read_text
from pdf_documents import Printed, write_pdf, write_raw

ROW = [Printed(40.0, 100.0, "Atorvastatin", 9.0), Printed(250.0, 100.0, "Tablet 10 mg", 9.0)]


def pieces(data: bytes, page: int = 1):
    return read_text(read_pdf(data)).page(page).pieces


def test_a_piece_of_text_sits_where_the_page_prints_it():
    first, second = pieces(write_pdf([ROW]))
    assert (first.text, first.x, first.y, first.size) == ("Atorvastatin", 40.0, 100.0, 9.0)
    assert (second.text, second.x) == ("Tablet 10 mg", 250.0)
    assert first.right == pytest.approx(40.0 + 12 * 4.5)  # twelve letters half an em wide


def test_the_page_is_measured_from_its_top_left_corner():
    page = read_text(read_pdf(write_pdf([ROW]))).page(1)
    assert (page.width, page.height) == (595.0, 842.0)
    assert not page.is_scan


def test_a_subset_font_is_read_through_the_table_it_carries():
    assert [piece.text for piece in pieces(write_pdf([ROW], font="codes"))] == [
        "Atorvastatin",
        "Tablet 10 mg",
    ]


def test_a_font_that_names_its_glyphs_is_read_through_their_names():
    assert [piece.text for piece in pieces(write_pdf([ROW], font="differences"))] == [
        "Atorvastatin",
        "Tablet 10 mg",
    ]


def test_a_code_no_font_accounts_for_is_shown_as_such_and_counted():
    data = write_raw(
        b"BT /F1 9 Tf 1 0 0 1 40 700 Tm <41FF42> Tj ET",
        objects=[
            b"<< /Type /Font /Subtype /Type0 /BaseFont /Sub /Encoding /Identity-H"
            b" /DescendantFonts [1 0 R] >>"
        ],
        resources=b"<< /Font << /F1 2 0 R >> >>",
    )
    reading = read_text(read_pdf(data))
    (piece,) = reading.page(1).pieces
    assert piece.text == UNREADABLE * 2
    assert reading.unreadable == 2


def test_kerning_inside_one_string_does_not_break_it_up():
    data = write_raw(b"BT /F1 10 Tf 1 0 0 1 40 700 Tm [(Ator) -20 (vastatin)] TJ ET")
    (piece,) = pieces(data)
    assert piece.text == "Atorvastatin"


def test_a_wide_jump_inside_one_string_starts_the_next_column():
    data = write_raw(b"BT /F1 10 Tf 1 0 0 1 40 700 Tm [(Atorvastatin) -12000 (Tablet)] TJ ET")
    first, second = pieces(data)
    assert (first.text, second.text) == ("Atorvastatin", "Tablet")
    assert second.x == pytest.approx(40 + 12 * 5 + 120)


def test_text_is_placed_by_the_matrix_that_draws_it():
    data = write_raw(b"q 2 0 0 2 10 10 cm BT /F1 10 Tf 1 0 0 1 40 380 Tm (Big) Tj ET Q")
    (piece,) = pieces(data)
    assert (piece.x, piece.size) == (90.0, 20.0)
    assert piece.y == pytest.approx(842 - 770)


def test_a_line_of_text_moves_down_by_the_leading():
    data = write_raw(b"BT /F1 10 Tf 12 TL 1 0 0 1 40 700 Tm (One) Tj T* (Two) Tj ET")
    first, second = pieces(data)
    assert (first.text, second.text) == ("One", "Two")
    assert second.y - first.y == 12.0


def test_text_drawn_inside_a_form_is_found_with_the_rest():
    form = b"BT /F1 9 Tf 1 0 0 1 0 0 Tm (Inside a form) Tj ET"
    data = write_raw(
        b"q 1 0 0 1 100 500 cm /Fm1 Do Q",
        objects=[
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 200 20] /Resources"
            b" << /Font << /F1 1 0 R >> >> /Length " + str(len(form)).encode() + b" >>"
            b"\nstream\n" + form + b"\nendstream"
        ],
        resources=b"<< /XObject << /Fm1 2 0 R >> /Font << /F1 1 0 R >> >>",
    )
    (piece,) = pieces(data)
    assert (piece.text, piece.x, piece.y) == ("Inside a form", 100.0, 342.0)


def test_the_bytes_of_an_inline_image_are_not_read_as_operators():
    data = write_raw(
        b"BT /F1 9 Tf 1 0 0 1 40 700 Tm (Before) Tj ET\n"
        b"BI /W 2 /H 2 /BPC 8 /CS /G ID \x00(Tj)\xff\xff EI\n"
        b"BT /F1 9 Tf 1 0 0 1 40 680 Tm (After) Tj ET"
    )
    assert [piece.text for piece in pieces(data)] == ["Before", "After"]


def test_a_page_turned_a_quarter_turn_reads_across():
    data = write_pdf([ROW], rotate=90)
    first, second = pieces(data)
    assert (first.text, first.x, first.y) == ("Atorvastatin", 40.0, 100.0)
    assert (second.x, second.y) == (250.0, 100.0)


def test_a_page_with_no_text_is_a_scan():
    reading = read_text(read_pdf(write_pdf([[], ROW])))
    assert reading.scans == (1,)
    assert reading.page(1).is_scan and not reading.page(2).is_scan


def test_a_page_that_is_not_there_says_so():
    reading = read_text(read_pdf(write_pdf([ROW])))
    with pytest.raises(PdfError, match="no page 4"):
        reading.page(4)


def test_a_file_that_asks_for_a_password_is_not_read():
    data = write_pdf([ROW]).replace(b"/Size", b"/Encrypt 99 0 R /Size")
    with pytest.raises(PdfError, match="encrypted"):
        read_text(read_pdf(data))
