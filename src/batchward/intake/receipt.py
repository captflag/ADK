"""Receiving a delivery: the invoice matched against the order and the godown's count.

An invoice that checks as ready (ADR 0015) says what the company billed. Before
it is posted, it is matched against two other records (ADR 0016):

- **the purchase order**: what was asked for. A product not ordered, or more
  than was ordered, is a person's call to accept. An order may come on several
  bills, so what earlier approved bills received against it counts too (ADR 0017).
- **the count**: what the godown in-charge counted off the boxes, batch by batch,
  with the expiry and manufacture month read from the packs. Units billed but
  not received go on a debit note to the company. Units received beyond what
  was billed and given free are not posted until someone decides.

What is posted is what arrived: received units up to those billed, then free
units. A batch new to the business also needs its manufacture month, which the
count sheet carries from the pack.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from batchward.core.models import Batch, BatchKey
from batchward.core.orders import PurchaseOrder
from batchward.core.printed import expiry_month
from batchward.intake.checks import CheckedLine, Finding, InvoiceCheck, Severity
from batchward.intake.invoice import amount

PAISA = Decimal("0.01")

_COUNT_COLUMNS = {
    "batchno": "batch_no",
    "batch": "batch_no",
    "batchnumber": "batch_no",
    "expiry": "expiry",
    "exp": "expiry",
    "mfg": "manufactured",
    "manufactured": "manufactured",
    "dateofmanufacture": "manufactured",
    "unitscounted": "units",
    "counted": "units",
    "units": "units",
    "product": "product",
}


class CountSheetError(ValueError):
    """A count sheet that cannot be read, naming the line."""


@dataclass(frozen=True, slots=True)
class CountedBatch:
    line: int
    batch_no: str
    units: int
    expiry: str = ""
    """As read off the pack."""
    manufactured: str = ""
    """As read off the pack; needed for a batch new to the business."""
    product: str = ""


@dataclass(frozen=True, slots=True)
class ReceivedLine:
    invoice_line: CheckedLine
    counted: int
    paid: int
    """Units posted at the invoice rate: received units up to those billed."""
    free: int
    """Free units posted, from what arrived beyond the units billed."""
    short: int
    """Billed units that did not arrive: the debit note."""
    manufactured: date | None
    """For a batch new to the business, the manufacture month from the pack."""


@dataclass(frozen=True, slots=True)
class DebitNoteLine:
    invoice_line: CheckedLine
    units: int
    taxable_value: Decimal
    tax_amount: Decimal

    @property
    def total(self) -> Decimal:
        return self.taxable_value + self.tax_amount


@dataclass(frozen=True, slots=True)
class Receipt:
    check: InvoiceCheck
    order: PurchaseOrder | None
    lines: tuple[ReceivedLine, ...]
    debit_note: tuple[DebitNoteLine, ...]
    findings: tuple[Finding, ...]
    """What matching found, beside the invoice check's own findings."""
    earlier: Mapping[str, int] | None = None
    """Units of each item earlier bills received against the order; None if not known."""

    @property
    def ready(self) -> bool:
        """Ready to post: the invoice checks, and matching needs nobody's decision."""
        return self.check.ready and not any(
            f.severity in (Severity.FIX, Severity.ASK) for f in self.findings
        )

    @property
    def debit_total(self) -> Decimal:
        return sum((line.total for line in self.debit_note), Decimal(0))

    def received(self) -> dict[str, int]:
        """Units of each item this delivery takes against its order: billed units that arrived.

        Free units are not counted: an order asks for units at the invoice rate.
        """
        units: dict[str, int] = {}
        for line in self.lines:
            if line.paid:
                item_id = line.invoice_line.item.id
                units[item_id] = units.get(item_id, 0) + line.paid
        return units

    def still_due(self) -> dict[str, int] | None:
        """Units of each item on the order not yet received, counting this delivery.

        None without an order, or when what earlier bills received is not known.
        """
        if self.order is None or self.earlier is None:
            return None
        now = self.received()
        return {
            line.item_id: max(
                0, line.quantity - self.earlier.get(line.item_id, 0) - now.get(line.item_id, 0)
            )
            for line in self.order.lines
        }


def read_count_sheet(lines: Iterable[str]) -> list[CountedBatch]:
    """The godown's count in CSV: at least each batch number and the units counted."""
    reader = csv.reader(lines)
    header = next(reader, None)
    if header is None:
        raise CountSheetError("the count sheet is empty")
    positions: dict[str, int] = {}
    for index, title in enumerate(header):
        key = _COUNT_COLUMNS.get(re.sub(r"[^a-z]", "", title.lower()))
        if key is not None:
            positions.setdefault(key, index)
    for key, title in (("batch_no", "Batch No."), ("units", "Units Counted")):
        if key not in positions:
            raise CountSheetError(f"the count sheet has no column {title!r}")
    counted = []
    for number, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue
        values = {
            key: cells[index].strip() if index < len(cells) else ""
            for key, index in positions.items()
        }
        if not values["batch_no"]:
            raise CountSheetError(f"line {number} has no batch number")
        if not re.fullmatch(r"\d+", values["units"].replace(",", "")):
            raise CountSheetError(f"line {number} counts {values['units']!r}, not whole units")
        counted.append(
            CountedBatch(
                line=number,
                batch_no=values["batch_no"],
                units=int(values["units"].replace(",", "")),
                expiry=values.get("expiry", ""),
                manufactured=values.get("manufactured", ""),
                product=values.get("product", ""),
            )
        )
    return counted


def match_receipt(
    check: InvoiceCheck,
    *,
    order: PurchaseOrder | None,
    counted: Iterable[CountedBatch],
    batches: Mapping[BatchKey, Batch],
    earlier: Mapping[str, int] | None = None,
) -> Receipt:
    """The delivery as the invoice, the order and the count together say it arrived.

    ``earlier`` is what other approved bills already received against the order, by
    item id. Without it, an order billed twice over several bills is not caught.
    """
    findings: list[Finding] = []

    def found(severity: Severity, line: int | None, field: str, message: str) -> None:
        findings.append(Finding(severity, line, field, message))

    by_batch: dict[str, list[CountedBatch]] = {}
    for count in counted:
        by_batch.setdefault(_normal(count.batch_no), []).append(count)

    received: list[ReceivedLine] = []
    debit: list[DebitNoteLine] = []
    for line in check.lines:
        counts = by_batch.pop(line.batch.batch_no, [])
        if not counts:
            found(
                Severity.ASK,
                line.number,
                "count",
                f"batch {line.batch.batch_no} of {line.item.brand} was not counted",
            )
            continue
        units = sum(count.units for count in counts)
        _check_packs(line, counts, batches, found)
        manufactured = _manufactured(line, counts, batches, found)
        paid = min(units, line.quantity)
        free = min(units - paid, line.free_quantity)
        short = line.quantity - paid
        if units > line.quantity + line.free_quantity:
            found(
                Severity.ASK,
                line.number,
                "count",
                f"{units} units of batch {line.batch.batch_no} counted, but only "
                f"{line.quantity} billed and {line.free_quantity} free: return or have "
                "the extra billed",
            )
        elif paid == line.quantity and free < line.free_quantity:
            found(
                Severity.NOTE,
                line.number,
                "count",
                f"{line.free_quantity - free} free units of batch {line.batch.batch_no} did "
                "not arrive; take it up with the company's representative",
            )
        if short:
            invoice_line = check.invoice.lines[line.number - 1]
            discount = amount(invoice_line.discount_percent or "0") or Decimal(0)
            taxable = (short * line.rate * (1 - discount / 100)).quantize(PAISA)
            gst = amount(invoice_line.gst_percent) or Decimal(0)
            debit.append(DebitNoteLine(line, short, taxable, (taxable * gst / 100).quantize(PAISA)))
            found(
                Severity.NOTE,
                line.number,
                "count",
                f"{short} billed units of batch {line.batch.batch_no} did not arrive; "
                "they go on the debit note",
            )
        received.append(ReceivedLine(line, units, paid, free, short, manufactured))

    for counts in by_batch.values():
        for count in counts:
            found(
                Severity.ASK,
                None,
                "count",
                f"count sheet line {count.line}: batch {count.batch_no} was counted but is "
                "not on the invoice",
            )
    _match_order(check, order, earlier, found)
    return Receipt(
        check=check,
        order=order,
        lines=tuple(received),
        debit_note=tuple(debit),
        findings=tuple(findings),
        earlier=None if earlier is None or order is None else dict(earlier),
    )


def _check_packs(line: CheckedLine, counts, batches, found) -> None:
    for count in counts:
        if not count.expiry:
            continue
        on_pack = expiry_month(count.expiry)
        if on_pack is None:
            found(
                Severity.ASK,
                line.number,
                "count",
                f"count sheet line {count.line}: expiry {count.expiry!r} does not read",
            )
        elif (on_pack.year, on_pack.month) != (line.batch.expiry.year, line.batch.expiry.month):
            found(
                Severity.ASK,
                line.number,
                "expiry",
                f"the pack of batch {line.batch.batch_no} says expiry {on_pack:%m/%Y}, the "
                f"invoice {line.batch.expiry:%m/%Y}",
            )


def _manufactured(line: CheckedLine, counts, batches, found) -> date | None:
    if line.batch in batches:
        return None
    printed = [count.manufactured for count in counts if count.manufactured]
    month = expiry_month(printed[0]) if printed else None
    if month is None:
        found(
            Severity.ASK,
            line.number,
            "manufactured",
            f"batch {line.batch.batch_no} is new: read its manufacture month off the pack",
        )
        return None
    first_day = month.replace(day=1)
    if first_day >= line.batch.expiry:
        found(
            Severity.ASK,
            line.number,
            "manufactured",
            f"batch {line.batch.batch_no}: manufactured {month:%m/%Y} is not before its "
            f"expiry {line.batch.expiry:%m/%Y}",
        )
        return None
    return first_day


def _match_order(
    check: InvoiceCheck, order: PurchaseOrder | None, earlier: Mapping[str, int] | None, found
) -> None:
    printed = check.invoice.order_no.strip()
    if order is None:
        found(
            Severity.NOTE,
            None,
            "order_no",
            f"no purchase order to match{f' ({printed})' if printed else ''}",
        )
        return
    if printed and _normal(printed) != _normal(order.number):
        found(
            Severity.ASK,
            None,
            "order_no",
            f"the invoice quotes order {printed}, not {order.number}",
        )
    if check.supplier is not None and check.supplier.id != order.company_id:
        found(
            Severity.ASK,
            None,
            "order_no",
            f"order {order.number} was placed with another company",
        )
    billed: dict[str, int] = {}
    for line in check.lines:
        billed[line.item.id] = billed.get(line.item.id, 0) + line.quantity
        ordered = order.ordered(line.item.id)
        if not ordered:
            found(
                Severity.ASK,
                line.number,
                "order_no",
                f"{line.item.brand} is not on order {order.number}",
            )
    if earlier is None:
        found(
            Severity.NOTE,
            None,
            "order_no",
            f"what earlier bills received against order {order.number} was not looked up, "
            "so billing beyond the order over several bills would not be caught",
        )
    for item_id, units in billed.items():
        ordered = order.ordered(item_id)
        before = (earlier or {}).get(item_id, 0)
        if ordered and units + before > ordered:
            number = next(line.number for line in check.lines if line.item.id == item_id)
            found(
                Severity.ASK,
                number,
                "quantity",
                f"{units} units billed, but {ordered} ordered on {order.number}"
                + (f" and {before} already received on earlier bills" if before else ""),
            )


def _normal(text: str) -> str:
    return "".join(text.split()).upper()
