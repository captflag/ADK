"""What an approved receipt produces: a purchase voucher for Marg to import, and a debit note.

Batchward never writes Marg's tables (ADR 0006). A received delivery is posted
by writing a file for Marg's purchase import, one row per batch with the units
that actually arrived, and only once a person has approved it (ADR 0016). Marg
publishes no import layout, so the columns here are an assumption, like the
table layout the bridge reads.

Units billed that did not arrive go on a debit note to the company, drafted for
the stockist to number, sign and send.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from batchward.bridge.marg_layout import format_expiry
from batchward.intake.checks import InvoiceCheck
from batchward.intake.invoice import amount
from batchward.intake.receipt import Receipt
from batchward.reporting.inr import format_inr

PAISA = Decimal("0.01")

VOUCHER_COLUMNS = (
    "SUPPLIER GSTIN",
    "SUPPLIER",
    "BILL NO",
    "BILL DATE",
    "ORDER NO",
    "PRODUCT CODE",
    "PRODUCT",
    "BATCH",
    "EXPIRY",
    "MFG",
    "QTY",
    "FREE",
    "MRP",
    "RATE",
    "DISC %",
    "GST %",
    "TAXABLE",
    "GST AMOUNT",
)


def approval_id(check: InvoiceCheck) -> str:
    """What approving a bill approves: one supplier's bill, whatever its form."""
    invoice = check.invoice
    supplier = check.supplier.id if check.supplier else invoice.supplier_name
    return f"purchase:{supplier}:{''.join(invoice.invoice_no.split()).upper()}"


def purchase_voucher(receipt: Receipt) -> str:
    """The Marg purchase import rows for what arrived, as CSV."""
    if not receipt.ready:
        raise ValueError("a receipt that is not ready to post has no voucher")
    invoice = receipt.check.invoice
    bill_date = receipt.check.invoice_date
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(VOUCHER_COLUMNS)
    for received in receipt.lines:
        line = received.invoice_line
        if received.paid == 0 and received.free == 0:
            continue
        printed = invoice.lines[line.number - 1]
        discount = amount(printed.discount_percent or "0") or Decimal(0)
        gst = amount(printed.gst_percent) or Decimal(0)
        taxable = (received.paid * line.rate * (1 - discount / 100)).quantize(PAISA)
        writer.writerow(
            (
                invoice.supplier_gstin,
                receipt.check.supplier.name if receipt.check.supplier else invoice.supplier_name,
                invoice.invoice_no,
                f"{bill_date:%d/%m/%Y}",
                invoice.order_no,
                line.item.id,
                line.item.brand,
                line.batch.batch_no,
                format_expiry(line.batch.expiry),
                "" if received.manufactured is None else f"{received.manufactured:%m/%Y}",
                received.paid,
                received.free,
                f"{line.mrp:.2f}",
                f"{line.rate:.2f}",
                f"{discount.normalize():f}",
                f"{gst.normalize():f}",
                f"{taxable:.2f}",
                f"{(taxable * gst / 100).quantize(PAISA):.2f}",
            )
        )
    return out.getvalue()


def debit_note(receipt: Receipt, *, received: date) -> str | None:
    """A draft debit note for billed units that did not arrive, or None if all arrived."""
    if not receipt.debit_note:
        return None
    invoice = receipt.check.invoice
    supplier = receipt.check.supplier
    lines = [
        "DEBIT NOTE",
        "Draft for the stockist to number, sign and send. Nothing has been sent.",
        "",
        f"{'To':12}{supplier.name if supplier else invoice.supplier_name}",
        f"{'GSTIN':12}{invoice.supplier_gstin or '-'}",
        f"{'Against':12}invoice {invoice.invoice_no} dated {invoice.invoice_date}"
        + (f", our order {invoice.order_no}" if invoice.order_no else ""),
        f"{'Number':12}[debit note number]",
        "",
        f"Billed but not received when the delivery was counted on {received:%d/%m/%Y}:",
        f"  {'Product':22}{'Batch':10}{'Units':>6}{'Rate':>10}{'Taxable':>14}{'GST':>12}"
        f"{'Total':>14}",
    ]
    for short in receipt.debit_note:
        line = short.invoice_line
        lines.append(
            f"  {line.item.brand:22}{line.batch.batch_no:10}{short.units:>6}"
            f"{format_inr(line.rate, paise=True):>10}"
            f"{format_inr(short.taxable_value, paise=True):>14}"
            f"{format_inr(short.tax_amount, paise=True):>12}"
            f"{format_inr(short.total, paise=True):>14}"
        )
    lines += [
        f"  {'Total':74}{format_inr(receipt.debit_total, paise=True):>14}",
        "",
        "Signature: ______________________",
    ]
    return "\n".join(lines) + "\n"
