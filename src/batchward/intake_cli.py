"""``batchward intake``: read supplier paperwork and check it before it is posted.

``intake check`` checks an invoice already in the intake schema. ``intake
extract`` has the clerk read an invoice photo, PDF or text into the schema,
checks the reading, and has fields read again where needed (ADR 0015). Only
extraction calls a model, so only it needs an API key. ``intake receive``
matches a reading against its purchase order, what earlier bills received
against that order, and the godown's count (ADR 0016, 0017). With ``--out`` a
delivery ready to post is put up for approval, and once a person approves it,
the purchase voucher for Marg to import and any debit note are written (ADR 0005).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from batchward.agents import receiving
from batchward.agents.data import DataUnavailableError, StockData, load_marg
from batchward.approvals_cli import request_approval
from batchward.bridge.marg_contract import MargLayoutError
from batchward.intake.checks import Finding, InvoiceCheck, Severity, check_invoice
from batchward.intake.delivery import Delivery, match_delivery
from batchward.intake.extract import document_part, extract_invoice
from batchward.intake.invoice import PurchaseInvoice
from batchward.intake.posting import posting
from batchward.intake.receipt import Receipt
from batchward.records.store import RecordsError, RecordStore
from batchward.reporting.inr import format_inr
from batchward.whatsapp_cli import notifier

_HEADINGS = {
    Severity.FIX: "Extract again: these fields do not read or do not add up",
    Severity.ASK: "Needs a person to decide",
    Severity.NOTE: "Worth knowing",
}
_EXPECTED = (
    DataUnavailableError,
    MargLayoutError,
    RecordsError,
    OSError,
    ValueError,
    sqlite3.Error,
)


def add_intake_commands(commands: argparse._SubParsersAction) -> None:
    intake = commands.add_parser("intake", help="read and check supplier paperwork before posting")
    actions = intake.add_subparsers(dest="intake_command", required=True)

    check = actions.add_parser(
        "check", help="check an invoice extracted as JSON against the stock records"
    )
    check.add_argument("invoice", type=Path, help="the invoice as JSON, in the intake schema")
    _records(check)
    check.set_defaults(handler=_check)

    extract = actions.add_parser(
        "extract",
        help="read an invoice photo, PDF or text with the clerk, check it, read again if needed",
    )
    extract.add_argument("document", type=Path, help="the invoice: .jpg, .png, .webp, .pdf or .txt")
    _records(extract)
    extract.add_argument("--out", type=Path, help="write the final reading here as JSON")
    extract.set_defaults(handler=_extract)

    receive = actions.add_parser(
        "receive",
        help="match a reading against its order and the godown count; post it once approved",
    )
    receive.add_argument("invoice", type=Path, help="the invoice reading as JSON")
    _records(receive)
    receive.add_argument("--count", type=Path, required=True, help="the godown's count as CSV")
    receive.add_argument(
        "--order",
        type=Path,
        help="the purchase order as JSON; by default, the order the invoice quotes, if placed "
        "through Batchward",
    )
    receive.add_argument(
        "--out",
        type=Path,
        help="put the delivery up for approval; the voucher and debit note are written here",
    )
    receive.add_argument(
        "--approve-by", help="approve it at once, as this person, and write the files"
    )
    receive.add_argument(
        "--notify",
        action="store_true",
        help="send the request to the approvers on WhatsApp (see `batchward whatsapp`)",
    )
    receive.add_argument(
        "--approvers", type=Path, help="CSV of phone,name to notify (default BATCHWARD_APPROVERS)"
    )
    receive.set_defaults(handler=_receive)


def _records(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--marg", type=Path, required=True, help="Marg database to check against")
    parser.add_argument(
        "--records", type=Path, help="Batchward records database, for recall blocks and ceilings"
    )
    parser.add_argument(
        "--received", type=date.fromisoformat, help="the day it arrived; default today"
    )


def _check(args: argparse.Namespace) -> int:
    try:
        invoice = PurchaseInvoice.model_validate_json(args.invoice.read_bytes())
        check = _checker(args)
    except ValidationError as error:
        first = error.errors()[0]
        print(
            f"batchward intake: {args.invoice} is not an invoice in the intake schema: "
            f"{error.error_count()} problems, first: {first['msg']} at "
            f"{'.'.join(map(str, first['loc']))}",
            file=sys.stderr,
        )
        return 1
    except _EXPECTED as error:
        print(f"batchward intake: {error}", file=sys.stderr)
        return 1
    _print_check(check(invoice))
    return 0


def _extract(args: argparse.Namespace) -> int:
    if not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")):
        print(
            "batchward intake: reading a document needs a Gemini API key. Put GOOGLE_API_KEY "
            "in .env and run with `uv run --env-file .env batchward intake extract ...`.",
            file=sys.stderr,
        )
        return 1
    try:
        document = document_part(args.document)
        check = _checker(args)
    except _EXPECTED as error:
        print(f"batchward intake: {error}", file=sys.stderr)
        return 1
    extraction = asyncio.run(extract_invoice(document, check=check))
    print(f"Read {args.document.name} in {extraction.attempts} {_attempts(extraction.attempts)}.")
    if extraction.invoice is None:
        print(f"batchward intake: nothing usable was read: {extraction.problem}", file=sys.stderr)
        return 1
    if args.out is not None:
        try:
            args.out.write_text(extraction.invoice.model_dump_json(indent=2) + "\n", "utf-8")
        except OSError as error:
            print(f"batchward intake: cannot write {args.out}: {error}", file=sys.stderr)
            return 1
        print(f"Wrote the reading to {args.out}")
    _print_check(extraction.check)
    return 0


def _receive(args: argparse.Namespace) -> int:
    asking = args.out is not None or args.approve_by is not None
    if asking and (args.out is None or args.records is None):
        print(
            "batchward intake: asking for approval needs --out for the files and --records "
            "for the request",
            file=sys.stderr,
        )
        return 1
    delivery = Delivery(
        invoice=args.invoice,
        count=args.count,
        marg=args.marg,
        received_on=args.received or date.today(),
        order=args.order,
        records=args.records,
    )
    try:
        receipt, stock = match_delivery(delivery)
    except ValidationError as error:
        print(
            f"batchward intake: {args.invoice} is not an invoice reading: {error}",
            file=sys.stderr,
        )
        return 1
    except _EXPECTED as error:
        print(f"batchward intake: {error}", file=sys.stderr)
        return 1

    _print_check(receipt.check)
    _print_receipt(receipt, stock)
    if not asking:
        if receipt.ready:
            print(
                "Nothing written. Put it up for approval with --out <folder>, or approve it "
                "at once with --approve-by <name> --out <folder>."
            )
        return 0
    if not receipt.ready:
        print(
            "batchward intake: not put up for approval: the receipt is not ready to post",
            file=sys.stderr,
        )
        return 1
    planned = posting(receipt, received_on=delivery.received_on)
    return request_approval(
        planned,
        records=args.records,
        start=lambda: receiving.ask_to_post(delivery, planned, out=args.out),
        approve_by=args.approve_by,
        notify=notifier(args),
    )


def _print_receipt(receipt: Receipt, stock: StockData) -> None:
    print("Received against the count:")
    print(
        f"  {'Line':<6}{'Batch':<10}{'Billed':>8}{'Free':>6}{'Counted':>9}{'Post':>7}{'Short':>7}"
        f"{'Damaged':>9}"
    )
    for line in receipt.lines:
        billed = line.invoice_line
        print(
            f"  {billed.number:<6}{billed.batch.batch_no:<10}{billed.quantity:>8}"
            f"{billed.free_quantity:>6}{line.counted:>9}{line.paid + line.free:>7}{line.short:>7}"
            f"{line.damaged:>9}"
        )
    _print_findings(receipt.findings)
    if receipt.debit_note:
        print(
            "Debit note for units not received or received damaged: "
            f"{format_inr(receipt.debit_total, paise=True)}"
        )
    if receipt.order is not None:
        print(f"Matched against order {receipt.order.number}.")
        _print_order(receipt, stock)
    print("Ready to post." if receipt.ready else "Not ready to post.")


def _print_order(receipt: Receipt, stock: StockData) -> None:
    """The order line by line: earlier bills, this delivery, and what is still due."""
    due = receipt.still_due()
    if receipt.order is None or receipt.earlier is None or due is None:
        return
    now = receipt.received()
    print(f"  {'Product':<24}{'Ordered':>9}{'Earlier':>9}{'Now':>7}{'Due':>7}")
    for line in receipt.order.lines:
        item = stock.items.get(line.item_id)
        name = item.brand if item is not None else line.item_id
        print(
            f"  {name[:23]:<24}{line.quantity:>9}{receipt.earlier.get(line.item_id, 0):>9}"
            f"{now.get(line.item_id, 0):>7}{due[line.item_id]:>7}"
        )
    outstanding = sum(due.values())
    print(
        f"  {outstanding} units still due on the order."
        if outstanding
        else "  Nothing more is due on the order."
    )


def _print_findings(findings: tuple[Finding, ...]) -> None:
    for severity in Severity:
        chosen = [f for f in findings if f.severity is severity]
        if not chosen:
            continue
        print(f"{_HEADINGS[severity]}:")
        for finding in chosen:
            where = f"line {finding.line}" if finding.line else "invoice"
            print(f"  {where}, {finding.field}: {finding.message}")


def _checker(args: argparse.Namespace):
    stock: StockData = load_marg(args.marg)
    holds = ceilings = None
    if args.records is not None:
        with RecordStore(args.records, create=False) as store:
            holds, ceilings = store.hold_log(), store.ceiling_table()
    received = args.received or date.today()

    def check(invoice: PurchaseInvoice) -> InvoiceCheck:
        return check_invoice(
            invoice,
            items=stock.items,
            parties=stock.parties,
            batches=stock.batches,
            received=received,
            ledger=stock.ledger,
            ceilings=ceilings,
            holds=holds,
        )

    return check


def _print_check(result: InvoiceCheck) -> None:
    invoice = result.invoice
    supplier = result.supplier.name if result.supplier else "supplier not on record"
    print(
        f"Invoice {invoice.invoice_no or '(no number)'} dated {invoice.invoice_date}, "
        f"from {invoice.supplier_name} ({supplier}): {len(invoice.lines)} lines"
    )
    _print_findings(result.findings)
    if result.ready:
        print(f"Ready to post: {len(result.lines)} lines match items and read in full.")
    else:
        print("Not ready to post.")


def _attempts(count: int) -> str:
    return "attempt" if count == 1 else "attempts"
