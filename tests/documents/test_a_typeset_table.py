"""A table read out of a PDF this project did not write.

Every other test here reads a PDF the test builder made, which risks the reader
agreeing with itself. `typeset-notification.pdf` was laid out as a table in a
word processor and exported by it, so it carries what a published notification
carries: fonts subset with codes of their own, objects packed into an object
stream, a cross-reference stream, a heading wrapped over three lines, and a
column of figures set to the right of a centred title. It is not a real NPPA
notification — it is the shape of one, produced by software that knows nothing
about this reader.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from batchward.compliance.nppa import notification_table, read_notification
from batchward.documents.pdf import read_file
from batchward.documents.text import read_text

TYPESET = Path(__file__).parent / "typeset-notification.pdf"


def test_the_document_is_written_the_way_published_ones_are():
    document = read_file(TYPESET)
    assert document.of_type("ObjStm"), "objects packed into an object stream"
    assert document.of_type("XRef"), "a cross-reference stream, not a table"
    reading = read_text(document)
    assert reading.scans == () and reading.unreadable == 0


def test_its_table_is_read_as_the_rows_and_columns_it_shows():
    table = notification_table(TYPESET.read_bytes())
    assert table.titles == (
        "S. No.",
        "Name of the Scheduled Formulation",
        "Dosage form & Strength",
        "Unit",
        "Ceiling Price (Rs.)",
    )
    assert table.rows == (
        ("1.", "Amoxicillin and Clavulanic Acid", "Tablet 500 mg + 125 mg", "1 Tablet", "23.44"),
        ("2.", "Paracetamol", "Tablet 500 mg", "1 Tablet", "1.87"),
        ("3.", "Insulin glargine (rDNA origin)", "Injection 100 IU/ml", "1 ml", "86.00"),
        ("4.", "Azithromycin", "Tablet 500 mg", "1 Tablet", "17.02"),
    )
    assert table.pages == (1,)


def test_what_is_read_goes_straight_into_the_notification_reader():
    rows = read_notification(notification_table(TYPESET.read_bytes()).lines())
    assert [(row.formulation, row.price) for row in rows][:2] == [
        ("Amoxicillin and Clavulanic Acid", Decimal("23.44")),
        ("Paracetamol", Decimal("1.87")),
    ]
    assert rows[2].strength == "100 IU/ml"
