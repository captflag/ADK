"""A printed table read back out of a PDF, as rows and columns."""

from __future__ import annotations

import pytest

from batchward.documents.tables import TableError, table_from_pdf
from pdf_documents import Printed, lay_out, write_pdf

COLUMNS = [40.0, 80.0, 250.0, 380.0, 470.0]
HEADING = [
    ["S. No.", "Name of the", "Dosage form &", "Unit", "Ceiling Price"],
    ["", "Scheduled Formulation", "Strength", "", "(Rs.)"],
]
TITLES = [
    "Name of the Scheduled Formulation",
    "Dosage form & Strength",
    "Unit",
    "Ceiling Price (Rs.)",
]
ROWS = [
    [
        ["1."],
        ["Amoxicillin and", "Clavulanic Acid"],
        ["Tablet 500 mg + 125 mg"],
        ["1 Tablet"],
        ["23.44"],
    ],
    [["2."], ["Paracetamol"], ["Tablet 500 mg"], ["1 Tablet"], ["1.87"]],
]
GAZETTE = Printed(150.0, 40.0, "MINISTRY OF CHEMICALS AND FERTILIZERS", 11.0)


def table(pages, titles=TITLES, **written):
    return table_from_pdf(write_pdf(pages, **written), titles)


def test_reads_the_rows_under_the_heading_it_is_told_to_look_for():
    found = table([[GAZETTE, *lay_out(COLUMNS, HEADING, ROWS)]])
    assert found.titles == ("S. No.", *TITLES)
    assert found.rows == (
        ("1.", "Amoxicillin and Clavulanic Acid", "Tablet 500 mg + 125 mg", "1 Tablet", "23.44"),
        ("2.", "Paracetamol", "Tablet 500 mg", "1 Tablet", "1.87"),
    )
    assert found.pages == (1,)


def test_the_heading_may_be_printed_on_one_line():
    heading = [["S. No.", "Name of the Formulation", "Dosage form & Strength", "Unit", "Price"]]
    found = table([lay_out(COLUMNS, heading, ROWS)], titles=[*TITLES, "Name of the Formulation"])
    assert found.rows[1] == ("2.", "Paracetamol", "Tablet 500 mg", "1 Tablet", "1.87")


def test_a_title_set_in_a_narrow_column_may_take_three_lines():
    heading = [
        ["S. No.", "Name of the", "Dosage form", "Unit", "Ceiling Price"],
        ["", "Scheduled", "& Strength", "", "(Rs.)"],
        ["", "Formulation", "", "", ""],
    ]
    found = table([lay_out(COLUMNS, heading, ROWS)])
    assert found.titles[1] == "Name of the Scheduled Formulation"
    assert len(found.rows) == 2


def test_a_heading_of_one_line_does_not_swallow_the_first_row():
    heading = [["S. No.", "Name of the Scheduled Formulation", "Dosage form & Strength", "Unit",
                "Ceiling Price (Rs.)"]]  # fmt: skip
    found = table([lay_out(COLUMNS, heading, ROWS)])
    assert found.titles[-1] == "Ceiling Price (Rs.)"
    assert found.rows[0][0] == "1."


def test_the_table_is_written_as_the_csv_the_importers_read():
    found = table([lay_out(COLUMNS, HEADING, ROWS)])
    assert found.as_csv().splitlines()[:2] == [
        "S. No.,Name of the Scheduled Formulation,Dosage form & Strength,Unit,Ceiling Price (Rs.)",
        "1.,Amoxicillin and Clavulanic Acid,Tablet 500 mg + 125 mg,1 Tablet,23.44",
    ]
    assert found.lines()[0].endswith("\n")


def test_a_table_running_over_pages_is_one_table_without_its_page_furniture():
    pages = [
        [*lay_out(COLUMNS, HEADING, ROWS), Printed(280.0, 800.0, "Page 1 of 2", 9.0)],
        [*lay_out(COLUMNS, HEADING, ROWS), Printed(280.0, 800.0, "Page 2 of 2", 9.0)],
    ]
    found = table(pages)
    assert len(found.rows) == 4
    assert found.pages == (1, 2)
    assert all("Page" not in cell for row in found.rows for cell in row)


def test_figures_set_to_the_right_of_a_centred_title_stay_in_their_column():
    found = table([lay_out(COLUMNS, HEADING, ROWS, centre_last=True)])
    assert [row[-1] for row in found.rows] == ["23.44", "1.87"]
    assert all(row[-2] == "1 Tablet" for row in found.rows)


def test_a_compressed_page_and_a_subset_font_read_the_same():
    plain = table([lay_out(COLUMNS, HEADING, ROWS)])
    for written in ({"compress": True}, {"font": "codes"}, {"widths": None}):
        assert table([lay_out(COLUMNS, HEADING, ROWS)], **written).rows == plain.rows


def test_a_page_turned_a_quarter_turn_is_read_across():
    found = table([lay_out(COLUMNS, HEADING, ROWS)], rotate=90)
    assert found.rows[0][1] == "Amoxicillin and Clavulanic Acid"


def test_a_document_with_no_such_heading_says_what_it_looked_for():
    with pytest.raises(TableError, match="Ceiling Price"):
        table([[GAZETTE]])


def test_a_scan_is_not_guessed_at():
    with pytest.raises(TableError, match="it is a scan"):
        table([[]])


def test_no_titles_to_look_for_is_refused():
    with pytest.raises(TableError, match="no column titles"):
        table([lay_out(COLUMNS, HEADING, ROWS)], titles=[])


def test_every_spelling_of_a_title_may_be_given_for_the_column_it_means():
    spellings = {
        "nameoftheformulation": "formulation",
        "nameofthescheduledformulation": "formulation",
        "dosageformstrength": "dosage",
        "unit": "unit",
        "ceilingpricers": "price",
    }
    found = table_from_pdf(write_pdf([lay_out(COLUMNS, HEADING, ROWS)]), spellings)
    assert found.titles[1] == "Name of the Scheduled Formulation"
    assert len(found.rows) == 2
