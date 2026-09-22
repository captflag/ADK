"""A delivery's paperwork, read and matched in one go.

The invoice reading, the godown's count, the purchase order, Marg's stock and
Batchward's records are all read from files, so a delivery can be matched again
from the same paths later: when a person approves it hours after it was put up
for approval, it is posted only if matching it again gives exactly what they saw
(ADR 0005, 0016).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from batchward.agents.data import StockData, load_marg
from batchward.core.orders import PurchaseOrder
from batchward.intake.checks import check_invoice
from batchward.intake.invoice import PurchaseInvoice
from batchward.intake.receipt import Receipt, match_receipt, read_count_sheet
from batchward.intake.voucher import approval_id
from batchward.records.store import RecordStore


@dataclass(frozen=True, slots=True)
class Delivery:
    invoice: Path
    """The invoice reading, as JSON in the intake schema."""
    count: Path
    """The godown's count, as CSV."""
    marg: Path
    received_on: date
    order: Path | None = None
    """The order as JSON; without it, the order the invoice quotes is looked up in the records."""
    records: Path | None = None

    def to_state(self) -> dict[str, str | None]:
        """The delivery as plain text values, to keep in a paused run's state."""
        return {
            "invoice": str(self.invoice),
            "count": str(self.count),
            "marg": str(self.marg),
            "received_on": self.received_on.isoformat(),
            "order": None if self.order is None else str(self.order),
            "records": None if self.records is None else str(self.records),
        }

    @classmethod
    def from_state(cls, state: Mapping[str, str | None]) -> Delivery:
        def path(key: str) -> Path | None:
            value = state.get(key)
            return None if value is None else Path(value)

        return cls(
            invoice=Path(str(state["invoice"])),
            count=Path(str(state["count"])),
            marg=Path(str(state["marg"])),
            received_on=date.fromisoformat(str(state["received_on"])),
            order=path("order"),
            records=path("records"),
        )


def match_delivery(delivery: Delivery) -> tuple[Receipt, StockData]:
    """Read the paperwork and match it: the invoice checked, then matched to order and count.

    Raises what reading raises: pydantic's ValidationError for a reading not in the
    intake schema, CountSheetError or ValueError for an unusable count or order,
    OSError, RecordsError, and Marg's DataUnavailableError or MargLayoutError.
    """
    invoice = PurchaseInvoice.model_validate_json(delivery.invoice.read_bytes())
    with delivery.count.open(encoding="utf-8-sig", newline="") as lines:
        counted = read_count_sheet(lines)
    order = None if delivery.order is None else PurchaseOrder.from_json(delivery.order.read_bytes())
    stock = load_marg(delivery.marg)

    def check(holds=None, ceilings=None):
        return check_invoice(
            invoice,
            items=stock.items,
            parties=stock.parties,
            batches=stock.batches,
            received=delivery.received_on,
            ledger=stock.ledger,
            ceilings=ceilings,
            holds=holds,
        )

    earlier = None
    if delivery.records is None:
        checked = check()
    else:
        with RecordStore(delivery.records, create=False) as store:
            checked = check(store.hold_log(), store.ceiling_table())
            if order is None and invoice.order_no.strip():
                order = store.order(invoice.order_no)
            if order is not None:
                earlier = store.received_against(order.number, excluding=approval_id(checked))
    receipt = match_receipt(
        checked, order=order, counted=counted, batches=stock.batches, earlier=earlier
    )
    return receipt, stock
