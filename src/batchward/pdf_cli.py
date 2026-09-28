"""``batchward pdf``: read a published PDF's text layer, and the table it prints.

A notification or an alert list arrives as a PDF. Before anything is recorded
from one, a person should see exactly what Batchward reads out of it: `pdf
table` prints the table as CSV, which is the same CSV the importers take, so a
row the layout defeats can be corrected in a spreadsheet and imported from
there (ADR 0026). `pdf text` prints the text layer line by line, which is what
to look at when the table is not found at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from batchward.arguments import positive_int
from batchward.compliance.cdsco import alert_table
from batchward.compliance.nppa import notification_table
from batchward.documents.pdf import PdfError, read_file
from batchward.documents.tables import Table, TableError, lines_of
from batchward.documents.text import read_text

TABLES = {
    "nppa": ("an NPPA ceiling price notification", notification_table),
    "cdsco": ("a CDSCO drug alert list", alert_table),
}


def add_pdf_commands(commands: argparse._SubParsersAction) -> None:
    pdf = commands.add_parser("pdf", help="read a published PDF: its text, and the table it prints")
    actions = pdf.add_subparsers(dest="pdf_command", required=True)

    table = actions.add_parser("table", help="print the table a PDF prints, as CSV")
    table.add_argument("file", type=Path, help="the PDF as published")
    table.add_argument(
        "--for",
        dest="kind",
        required=True,
        choices=sorted(TABLES),
        help="which published table to look for",
    )
    table.add_argument("--out", type=Path, help="write the CSV here instead of printing it")
    table.set_defaults(handler=_table)

    text = actions.add_parser("text", help="print a PDF's text layer, line by line")
    text.add_argument("file", type=Path, help="the PDF as published")
    text.add_argument("--page", type=positive_int, help="one page only; default every page")
    text.add_argument(
        "--places", action="store_true", help="show where each piece of text sits, in points"
    )
    text.set_defaults(handler=_text)


def _table(args: argparse.Namespace) -> int:
    what, read = TABLES[args.kind]
    try:
        table = read(args.file.read_bytes())
    except (OSError, PdfError, TableError) as error:
        print(f"batchward pdf: {args.file.name} is not read as {what}: {error}", file=sys.stderr)
        return 1
    if args.out:
        try:
            args.out.write_text(table.as_csv(), encoding="utf-8", newline="")
        except OSError as error:
            print(f"batchward pdf: cannot write {args.out}: {error}", file=sys.stderr)
            return 1
        print(f"{args.file.name}: {_found(table)}")
        print(f"Written to {args.out}. Check it, then import it with --csv.")
    else:
        print(table.as_csv(), end="")
    _warn(table, args.file)
    return 0


def _text(args: argparse.Namespace) -> int:
    try:
        document = read_file(args.file)
        reading = read_text(document, pages=range(args.page, args.page + 1) if args.page else None)
    except PdfError as error:
        print(f"batchward pdf: {error}", file=sys.stderr)
        return 1
    for page in reading.pages:
        print(f"Page {page.number} ({page.width:.0f} x {page.height:.0f} points)")
        if page.is_scan:
            print("  no text: this page is a picture of a page, and is not read here")
            continue
        for line in lines_of(page):
            if args.places:
                for piece in line.pieces:
                    print(f"  x={piece.x:7.1f} y={piece.y:7.1f} {piece.size:5.1f}pt  {piece.text}")
            else:
                print(f"  {line.text}")
    return 0


def _found(table: Table) -> str:
    pages = ", ".join(str(number) for number in table.pages)
    rows = "1 row" if len(table.rows) == 1 else f"{len(table.rows)} rows"
    of = "page" if len(table.pages) == 1 else "pages"
    return f"{rows} under {len(table.columns)} columns, from {of} {pages}"


def read_note(table: Table, file: Path) -> str:
    """What was read out of a PDF, to print before what it is taken to mean."""
    note = f"Read from {file.name}: {_found(table)}."
    if table.unreadable:
        note += (
            f" {table.unreadable} characters are printed in a font that does not say what they "
            "are, and are shown as �."
        )
    return note + " Check it with `batchward pdf table` before relying on it."


def _warn(table: Table, file: Path) -> None:
    if table.unreadable:
        print(
            f"batchward pdf: {table.unreadable} characters in {file.name} are printed in a font "
            "that does not say what they are; they are shown as �",
            file=sys.stderr,
        )
