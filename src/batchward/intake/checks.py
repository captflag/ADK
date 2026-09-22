"""Checks on an extracted invoice before anything is posted (ADR 0015).

Every finding says what to do next:

- **fix**: a field does not read, or the invoice does not add up. The extraction
  is most likely wrong, so the finding goes back to it as a hint.
- **ask**: the paper reads, but a person must decide: a batch number that could
  be a misread one already held ("J4021 or J4O21?"), a batch under a recall
  block, a supplier or item not on record, stock that has already expired, or
  stock printed above its ceiling price that the Price Guard would not let be
  billed.
- **note**: the invoice can be posted, but someone should know: short-dated
  stock, a rate well off the usual, an HSN code that differs from the record.

An invoice is ready to post only when nothing needs fixing or asking.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from statistics import median

from batchward.compliance.prices import CeilingTable, Verdict, check_batch_price
from batchward.compliance.recall import looks_alike
from batchward.core.holds import HoldLog
from batchward.core.ledger import Ledger
from batchward.core.models import Batch, BatchKey, Item, MovementType, Party
from batchward.core.printed import expiry_month, printed_date
from batchward.intake.gstin import gstin_problem
from batchward.intake.invoice import InvoiceLine, PurchaseInvoice, amount, count
from batchward.intake.matching import match_item, match_supplier

GST_RATES = frozenset(Decimal(rate) for rate in (0, 5, 12, 18, 28))
TOLERANCE = Decimal("0.05")
"""The most a printed figure may differ from its arithmetic, for rounding to the paisa."""
SHORT_DATED_MONTHS = 6
RATE_OUTLIER = Decimal("0.15")
"""How far a rate may stray from the item's usual purchase rate before it is noted."""
STALE_INVOICE_DAYS = 180


class Severity(StrEnum):
    FIX = "fix"
    ASK = "ask"
    NOTE = "note"


@dataclass(frozen=True, slots=True)
class Finding:
    severity: Severity
    line: int | None
    """The invoice line, counting from 1, or None for the invoice as a whole."""
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class CheckedLine:
    number: int
    item: Item
    batch: BatchKey
    quantity: int
    free_quantity: int
    mrp: Decimal
    rate: Decimal


@dataclass(frozen=True, slots=True)
class InvoiceCheck:
    invoice: PurchaseInvoice
    supplier: Party | None
    invoice_date: date | None
    findings: tuple[Finding, ...]
    lines: tuple[CheckedLine, ...]
    """Lines that read in full and match one item."""

    @property
    def ready(self) -> bool:
        return not any(f.severity in (Severity.FIX, Severity.ASK) for f in self.findings)

    def hints(self) -> list[str]:
        """What to look at again when extracting the invoice a second time."""
        return [
            (f"line {f.line}, {f.field}: " if f.line else f"{f.field}: ") + f.message
            for f in self.findings
            if f.severity is Severity.FIX
        ]


def check_invoice(
    invoice: PurchaseInvoice,
    *,
    items: Mapping[str, Item],
    parties: Mapping[str, Party],
    batches: Mapping[BatchKey, Batch],
    received: date,
    ledger: Ledger | None = None,
    ceilings: CeilingTable | None = None,
    holds: HoldLog | None = None,
) -> InvoiceCheck:
    """Every problem with an extracted invoice, and the lines ready to post."""
    findings: list[Finding] = []

    def found(severity: Severity, line: int | None, field: str, message: str) -> None:
        findings.append(Finding(severity, line, field, message))

    supplier = match_supplier(invoice.supplier_name, invoice.supplier_gstin, parties.values())
    invoice_date = _check_header(invoice, supplier, received, found)

    held_numbers: defaultdict[str, list[BatchKey]] = defaultdict(list)
    for key in batches:
        held_numbers[key.item_id].append(key)
    rates = _usual_rates(ledger) if ledger is not None else {}

    checked: list[CheckedLine] = []
    seen: dict[tuple[str, str], tuple[int, date]] = {}
    for number, line in enumerate(invoice.lines, start=1):
        result = _check_line(
            number,
            line,
            found,
            invoice_date=invoice_date or received,
            received=received,
            supplier=supplier,
            items=items,
            held=held_numbers,
            batches=batches,
            rates=rates,
            ceilings=ceilings,
            holds=holds,
        )
        if result is None:
            continue
        identity = (result.item.id, result.batch.batch_no)
        if identity in seen:
            earlier, expiry = seen[identity]
            if expiry != result.batch.expiry:
                found(
                    Severity.FIX,
                    number,
                    "expiry",
                    f"batch {result.batch.batch_no} is on line {earlier} with expiry "
                    f"{expiry:%m/%Y}; one of the two expiries is misread",
                )
            else:
                found(
                    Severity.ASK,
                    number,
                    "batch_no",
                    f"batch {result.batch.batch_no} of {result.item.brand} is billed again on "
                    f"line {earlier}; is it billed twice?",
                )
        seen.setdefault(identity, (number, result.batch.expiry))
        checked.append(result)

    if not invoice.lines:
        found(Severity.FIX, None, "lines", "no product lines were read")
    _check_totals(invoice, found)
    return InvoiceCheck(
        invoice=invoice,
        supplier=supplier,
        invoice_date=invoice_date,
        findings=tuple(findings),
        lines=tuple(checked),
    )


def _check_header(invoice, supplier, received, found) -> date | None:
    if not invoice.supplier_name.strip():
        found(Severity.FIX, None, "supplier_name", "the supplier's name was not read")
    if not invoice.invoice_no.strip():
        found(Severity.FIX, None, "invoice_no", "the invoice number was not read")

    if not invoice.supplier_gstin.strip():
        found(
            Severity.ASK,
            None,
            "supplier_gstin",
            "no supplier GSTIN: input tax credit cannot be claimed without it",
        )
    elif problem := gstin_problem(invoice.supplier_gstin):
        found(Severity.FIX, None, "supplier_gstin", problem)
    elif (
        supplier is not None
        and supplier.gstin
        and (
            "".join(supplier.gstin.split()).upper()
            != "".join(invoice.supplier_gstin.split()).upper()
        )
    ):
        found(
            Severity.ASK,
            None,
            "supplier_gstin",
            f"{supplier.name} is on record with GSTIN {supplier.gstin}, not "
            f"{invoice.supplier_gstin}",
        )
    if invoice.buyer_gstin.strip() and (problem := gstin_problem(invoice.buyer_gstin)):
        found(Severity.FIX, None, "buyer_gstin", problem)

    if supplier is None:
        found(
            Severity.ASK,
            None,
            "supplier_name",
            f"no company on record matches {invoice.supplier_name!r}",
        )
    if not invoice.supplier_licence.strip() and not (supplier and supplier.drug_licence_no):
        found(
            Severity.ASK,
            None,
            "supplier_licence",
            "no supplier drug licence number: Rule 65 requires it on the purchase record",
        )

    invoice_date = printed_date(invoice.invoice_date)
    if invoice_date is None:
        found(Severity.FIX, None, "invoice_date", f"{invoice.invoice_date!r} is not a date")
    elif invoice_date > received:
        found(
            Severity.FIX,
            None,
            "invoice_date",
            f"dated {invoice_date:%d/%m/%Y}, after it was received on {received:%d/%m/%Y}",
        )
    elif received - invoice_date > timedelta(days=STALE_INVOICE_DAYS):
        found(
            Severity.NOTE,
            None,
            "invoice_date",
            f"dated {invoice_date:%d/%m/%Y}, more than {STALE_INVOICE_DAYS} days before it "
            "was received",
        )
    return invoice_date


def _check_line(
    number: int,
    line: InvoiceLine,
    found,
    *,
    invoice_date: date,
    received: date,
    supplier: Party | None,
    items: Mapping[str, Item],
    held: Mapping[str, list[BatchKey]],
    batches: Mapping[BatchKey, Batch],
    rates: Mapping[str, Decimal],
    ceilings: CeilingTable | None,
    holds: HoldLog | None,
) -> CheckedLine | None:
    unread = False

    def figure(field: str, text: str, reader, *, optional: bool = False):
        nonlocal unread
        if optional and not text.strip():
            return reader("0")
        value = reader(text)
        if value is None:
            unread = True
            found(Severity.FIX, number, field, f"{text!r} is not a readable {_NAMES[field]}")
        return value

    for field in ("description", "batch_no"):
        if not getattr(line, field).strip():
            unread = True
            found(Severity.FIX, number, field, f"the {_NAMES[field]} was not read")
    expiry = figure("expiry", line.expiry, expiry_month)
    quantity = figure("quantity", line.quantity, count)
    free = figure("free_quantity", line.free_quantity, count, optional=True)
    mrp = figure("mrp", line.mrp, amount)
    rate = figure("rate", line.rate, amount)
    discount = figure("discount_percent", line.discount_percent, amount, optional=True)
    gst = figure("gst_percent", line.gst_percent, amount)
    taxable = figure("taxable_value", line.taxable_value, amount)
    tax = figure("tax_amount", line.tax_amount, amount)

    if quantity == 0:
        unread = True
        found(Severity.FIX, number, "quantity", "a line bills no units")
    if gst is not None and gst not in GST_RATES:
        found(Severity.FIX, number, "gst_percent", f"{line.gst_percent}% is not a GST rate")
    if mrp is not None and rate is not None and rate > mrp:
        found(
            Severity.FIX,
            number,
            "rate",
            f"rate {rate} is above the MRP {mrp}; the two columns may be swapped",
        )
    if None not in (quantity, rate, discount, taxable):
        expected = (quantity * rate * (1 - discount / 100)).quantize(Decimal("0.01"))
        if abs(expected - taxable) > TOLERANCE:
            found(
                Severity.FIX,
                number,
                "taxable_value",
                f"{quantity} units at {rate} less {discount}% is {expected}, not {taxable}",
            )
    if None not in (taxable, gst, tax):
        expected = (taxable * gst / 100).quantize(Decimal("0.01"))
        if abs(expected - tax) > TOLERANCE:
            found(
                Severity.FIX,
                number,
                "tax_amount",
                f"{gst}% GST on {taxable} is {expected}, not {tax}",
            )
    if expiry is not None:
        if expiry <= invoice_date:
            found(
                Severity.ASK,
                number,
                "expiry",
                f"expiry {expiry:%m/%Y} is not after the invoice date: expired stock, or misread",
            )
        elif _months_between(received, expiry) < SHORT_DATED_MONTHS:
            found(
                Severity.NOTE,
                number,
                "expiry",
                f"short-dated: expires {expiry:%m/%Y}, under {SHORT_DATED_MONTHS} months away",
            )

    match = match_item(
        line.description,
        items.values(),
        company_id=supplier.id if supplier else None,
    )
    if match.item is None:
        brands = ", ".join(f"{item.brand} ({item.unit})" for item in match.candidates[:3])
        found(
            Severity.ASK,
            number,
            "description",
            f"{line.description!r} could be {brands}"
            if match.candidates
            else f"no item on record matches {line.description!r}",
        )
        return None
    item = match.item
    if unread or expiry is None:
        return None

    if gst is not None and gst != item.gst_rate * 100:
        found(
            Severity.ASK,
            number,
            "gst_percent",
            f"billed at {gst}% GST, but {item.brand} is on record at {item.gst_rate * 100}%",
        )
    if line.hsn.strip() and line.hsn.strip()[:4] != item.hsn[:4]:
        found(Severity.NOTE, number, "hsn", f"HSN {line.hsn} differs from {item.hsn} on record")

    key = BatchKey(item.company_id, item.id, line.batch_no, expiry)
    same_number = [k for k in held.get(item.id, ()) if k.batch_no == key.batch_no]
    for other in same_number:
        if (other.expiry.year, other.expiry.month) != (expiry.year, expiry.month):
            found(
                Severity.FIX,
                number,
                "expiry",
                f"batch {key.batch_no} of {item.brand} is on record with expiry "
                f"{other.expiry:%m/%Y}, not {expiry:%m/%Y}",
            )
            return None
    if key in batches and batches[key].mrp != mrp:
        found(
            Severity.FIX,
            number,
            "mrp",
            f"batch {key.batch_no} is on record with MRP {batches[key].mrp}, not {mrp}",
        )
    for other in held.get(item.id, ()):
        if looks_alike(key.batch_no, other.batch_no):
            found(
                Severity.ASK,
                number,
                "batch_no",
                f"{key.batch_no} or {other.batch_no}? {item.brand} batch {other.batch_no} "
                "is already held and is easily misread as this one",
            )
    if holds is not None:
        for hold in holds.active(key):
            found(
                Severity.ASK,
                number,
                "batch_no",
                f"batch {key.batch_no} is blocked ({hold.reference}); do not accept it into "
                "sellable stock",
            )
    if ceilings is not None and item.dpco_scheduled:
        made = min(invoice_date, expiry - timedelta(days=1))
        check = check_batch_price(item, Batch(key, made, mrp), on=invoice_date, ceilings=ceilings)
        if check.verdict is Verdict.BLOCK:
            found(
                Severity.ASK,
                number,
                "mrp",
                f"accept stock that cannot be billed as printed? {check.reason}",
            )
    usual = rates.get(item.id)
    if usual and abs(rate - usual) > RATE_OUTLIER * usual:
        found(
            Severity.NOTE,
            number,
            "rate",
            f"rate {rate} is more than {RATE_OUTLIER:.0%} from the usual {usual} for {item.brand}",
        )
    return CheckedLine(number, item, key, quantity, free, mrp, rate)


def _check_totals(invoice: PurchaseInvoice, found) -> None:
    lines = invoice.lines
    taxable_total = amount(invoice.taxable_total)
    tax_total = amount(invoice.tax_total)
    round_off = amount(invoice.round_off) if invoice.round_off.strip() else Decimal(0)
    grand_total = amount(invoice.grand_total)
    for field, value, text in (
        ("taxable_total", taxable_total, invoice.taxable_total),
        ("tax_total", tax_total, invoice.tax_total),
        ("round_off", round_off, invoice.round_off),
        ("grand_total", grand_total, invoice.grand_total),
    ):
        if value is None:
            found(Severity.FIX, None, field, f"{text!r} is not a readable amount")
    taxable_lines = [amount(line.taxable_value) for line in lines]
    tax_lines = [amount(line.tax_amount) for line in lines]
    if taxable_total is not None and None not in taxable_lines:
        added = sum(taxable_lines, Decimal(0))
        if abs(added - taxable_total) > TOLERANCE:
            found(
                Severity.FIX,
                None,
                "taxable_total",
                f"the lines add up to {added}, not {taxable_total}",
            )
    if tax_total is not None and None not in tax_lines:
        added = sum(tax_lines, Decimal(0))
        if abs(added - tax_total) > TOLERANCE:
            found(
                Severity.FIX,
                None,
                "tax_total",
                f"the lines' GST adds up to {added}, not {tax_total}",
            )
    if round_off is not None and abs(round_off) >= 1:
        found(Severity.FIX, None, "round_off", f"a round off of {round_off} is a rupee or more")
    if None not in (taxable_total, tax_total, round_off, grand_total):
        expected = taxable_total + tax_total + round_off
        if abs(expected - grand_total) > TOLERANCE:
            found(
                Severity.FIX,
                None,
                "grand_total",
                f"{taxable_total} + {tax_total} GST + {round_off} round off is {expected}, "
                f"not {grand_total}",
            )


def _usual_rates(ledger: Ledger) -> dict[str, Decimal]:
    rates: defaultdict[str, list[Decimal]] = defaultdict(list)
    for m in ledger:
        if m.kind is MovementType.PURCHASE and m.rate is not None and not ledger.is_reversed(m.id):
            rates[m.batch.item_id].append(m.rate)
    return {item_id: median(values) for item_id, values in rates.items()}


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


_NAMES = {
    "description": "product description",
    "batch_no": "batch number",
    "expiry": "expiry month",
    "quantity": "whole number of units",
    "free_quantity": "whole number of free units",
    "mrp": "MRP",
    "rate": "rate",
    "discount_percent": "discount percent",
    "gst_percent": "GST percent",
    "taxable_value": "taxable value",
    "tax_amount": "GST amount",
}
