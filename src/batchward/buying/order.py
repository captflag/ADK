"""A purchase order on one company, drafted from suggestions and placed on approval (ADR 0020).

An order is numbered from the company and the day, like a claim, so drafting it
again that day drafts the same order. Approving it writes the order for the
company, as a sheet and as a message to send, and records it, so a delivery
that quotes its number is matched against it (ADR 0016) and what is still due
on it is counted before ordering again.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from batchward.analysis.costs import PAISA
from batchward.buying.suggest import Suggestion
from batchward.core.approvals import Posting, digest
from batchward.core.models import Party
from batchward.core.orders import OrderLine, PurchaseOrder
from batchward.reporting.inr import format_inr

KIND = "purchase order"
OPEN_DAYS = 30
"""How long an order's undelivered units are still counted as coming."""
ORDER_COLUMNS = (
    "ORDER NO", "DATE", "PRODUCT CODE", "PRODUCT", "PACK", "QTY", "RATE", "VALUE", "CASES",
    "UNITS PER CASE",
)  # fmt: skip
"""The order sheet's columns; the case columns are blank for a product with no case size."""


def order_number(company_id: str, on: date) -> str:
    return f"PO/{company_id}/{on:%y%m%d}"


def draft_order(
    suggestions: Iterable[Suggestion], *, company_id: str, on: date
) -> PurchaseOrder | None:
    """An order for everything of one company's that needs ordering, or None."""
    lines = sorted(
        (
            OrderLine(s.item.id, s.quantity)
            for s in suggestions
            if s.item.company_id == company_id and s.quantity > 0
        ),
        key=lambda line: line.item_id,
    )
    if not lines:
        return None
    return PurchaseOrder(order_number(company_id, on), company_id, on, tuple(lines))


def order_posting(
    order: PurchaseOrder, company: Party, suggestions: Mapping[str, Suggestion]
) -> Posting:
    """What approving an order writes: the order sheet and the message to send."""
    stem = order.number.replace("/", "-")
    total = sum(
        (value for line in order.lines if (value := _value(line, suggestions)) is not None),
        Decimal(0),
    )
    files = {
        f"{stem}.order.csv": _sheet(order, suggestions),
        f"{stem}.order.txt": _message(order, company, suggestions, total),
    }
    units = sum(line.quantity for line in order.lines)
    summary = (
        f"{order.number} on {company.name}: {units} units of {len(order.lines)} "
        f"{'product' if len(order.lines) == 1 else 'products'}, about "
        f"{format_inr(total, paise=True)} at last purchase rates"
    )
    return Posting(
        approval_id=f"order:{order.number}",
        files=files,
        summary=summary,
        digest=digest("".join(files[name] for name in sorted(files))),
    )


def _value(line: OrderLine, suggestions: Mapping[str, Suggestion]) -> Decimal | None:
    rate = suggestions[line.item_id].rate
    return None if rate is None else (rate * line.quantity).quantize(PAISA)


def _sheet(order: PurchaseOrder, suggestions: Mapping[str, Suggestion]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(ORDER_COLUMNS)
    for line in order.lines:
        item = suggestions[line.item_id].item
        rate = suggestions[line.item_id].rate
        case = suggestions[line.item_id].case_units
        value = _value(line, suggestions)
        writer.writerow(
            (
                order.number,
                f"{order.placed_on:%d/%m/%Y}",
                item.id,
                item.brand,
                item.unit,
                line.quantity,
                "" if rate is None else f"{rate:.2f}",
                "" if value is None else f"{value:.2f}",
                "" if case is None else line.quantity // case,
                "" if case is None else case,
            )
        )
    return out.getvalue()


def _message(
    order: PurchaseOrder, company: Party, suggestions: Mapping[str, Suggestion], total: Decimal
) -> str:
    lines = [
        f"Purchase order {order.number}, {order.placed_on:%d/%m/%Y}",
        f"To: {company.name}",
        "",
        "Please supply:",
    ]
    for line in order.lines:
        item = suggestions[line.item_id].item
        case = suggestions[line.item_id].case_units
        cases = (
            ""
            if case is None
            else f", {line.quantity // case} {'case' if line.quantity == case else 'cases'} "
            f"of {case}"
        )
        lines.append(f"  {item.brand} ({item.unit}): {line.quantity}{cases}")
    lines += [
        "",
        f"About {format_inr(total, paise=True)} at our last purchase rates.",
        f"Please quote {order.number} on your invoice.",
    ]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class OpenOrder:
    order: PurchaseOrder
    received: dict[str, int] = field(default_factory=dict)
    """Units of each item approved bills have received against it (ADR 0017)."""

    def due(self) -> dict[str, int]:
        return {
            line.item_id: line.quantity - self.received.get(line.item_id, 0)
            for line in self.order.lines
            if line.quantity > self.received.get(line.item_id, 0)
        }

    def age(self, on: date) -> int:
        return (on - self.order.placed_on).days


def still_due(
    orders: Iterable[OpenOrder], *, on: date, open_days: int = OPEN_DAYS
) -> tuple[dict[str, int], list[OpenOrder]]:
    """Units still coming on recent orders, by item, and older orders never delivered in full.

    An order older than ``open_days`` with units outstanding is not counted as
    coming: the company has not sent them, and someone should ask.
    """
    coming: dict[str, int] = {}
    overdue = []
    for order in orders:
        due = order.due()
        if not due:
            continue
        if order.age(on) > open_days:
            overdue.append(order)
            continue
        for item_id, units in due.items():
            coming[item_id] = coming.get(item_id, 0) + units
    return coming, sorted(overdue, key=lambda o: o.order.placed_on)
