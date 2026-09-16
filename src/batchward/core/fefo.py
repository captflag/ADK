"""First-expiry-first-out allocation of outgoing stock.

Allocation only chooses batches; it records nothing. The caller turns the
allocation into movements and appends them to the ledger.
"""

from collections.abc import Mapping
from datetime import date

from batchward.core.ledger import InsufficientStockError, Ledger
from batchward.core.models import Batch, BatchKey


def sellable_stock(
    ledger: Ledger,
    batches: Mapping[BatchKey, Batch],
    *,
    item_id: str,
    location_id: str,
    on: date,
) -> list[tuple[BatchKey, int]]:
    """Batches of an item that can be sold on a date, earliest expiry first."""
    available = []
    for key, qty in ledger.stock_of_item(item_id, location_id).items():
        batch = batches.get(key)
        if batch is None:
            raise LookupError(f"no batch record for {key}")
        if batch.is_sellable(on):
            available.append((key, qty))
    return sorted(available, key=lambda entry: (entry[0].expiry, entry[0].batch_no))


def allocate_fefo(
    ledger: Ledger,
    batches: Mapping[BatchKey, Batch],
    *,
    item_id: str,
    location_id: str,
    qty: int,
    on: date,
) -> list[tuple[BatchKey, int]]:
    """Choose which batches supply ``qty`` units, earliest expiry first."""
    if qty <= 0:
        raise ValueError("qty must be positive")
    allocation: list[tuple[BatchKey, int]] = []
    remaining = qty
    for key, available in sellable_stock(
        ledger, batches, item_id=item_id, location_id=location_id, on=on
    ):
        take = min(available, remaining)
        allocation.append((key, take))
        remaining -= take
        if remaining == 0:
            return allocation
    raise InsufficientStockError(
        f"{qty} units of item {item_id} requested at {location_id}, "
        f"but only {qty - remaining} are sellable on {on.isoformat()}"
    )
