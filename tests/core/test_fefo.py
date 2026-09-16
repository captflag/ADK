from datetime import date
from decimal import Decimal

import pytest

from batchward.core.fefo import allocate_fefo, sellable_stock
from batchward.core.ledger import InsufficientStockError, Ledger
from batchward.core.models import Batch, BatchStatus, MovementType
from factories import at, batch_key, movement

TODAY = date(2026, 6, 1)
OLD = batch_key("B-OLD", expiry=date(2026, 9, 30))
NEW = batch_key("B-NEW", expiry=date(2027, 9, 30))
EXPIRED = batch_key("B-EXP", expiry=date(2026, 5, 31))


def stocked(*entries, location_id="GODOWN"):
    """A ledger and batch records holding (key, qty, status) entries."""
    ledger, batches = Ledger(), {}
    for key, qty, *status in entries:
        batches[key] = Batch(
            key=key,
            manufactured=date(2025, 1, 1),
            mrp=Decimal(50),
            status=status[0] if status else BatchStatus.LIVE,
        )
        ledger.append(
            movement(MovementType.PURCHASE, qty, when=at(1), batch=key, location_id=location_id)
        )
    return ledger, batches


def allocate(ledger, batches, qty, *, item_id="I001", location_id="GODOWN", on=TODAY):
    return allocate_fefo(ledger, batches, item_id=item_id, location_id=location_id, qty=qty, on=on)


def test_takes_the_earliest_expiring_batch_first():
    ledger, batches = stocked((NEW, 100), (OLD, 100))
    assert allocate(ledger, batches, 30) == [(OLD, 30)]


def test_spills_into_the_next_batch_when_the_first_runs_out():
    ledger, batches = stocked((NEW, 100), (OLD, 20))
    assert allocate(ledger, batches, 50) == [(OLD, 20), (NEW, 30)]


def test_never_allocates_expired_stock():
    ledger, batches = stocked((EXPIRED, 100), (NEW, 10))
    assert allocate(ledger, batches, 10) == [(NEW, 10)]


@pytest.mark.parametrize("status", [BatchStatus.BLOCKED, BatchStatus.RECALLED])
def test_never_allocates_a_batch_that_is_not_live(status):
    ledger, batches = stocked((OLD, 100, status), (NEW, 10))
    assert allocate(ledger, batches, 10) == [(NEW, 10)]


def test_only_uses_stock_at_the_requested_location():
    ledger, batches = stocked((OLD, 100), location_id="COLD_ROOM")
    with pytest.raises(InsufficientStockError):
        allocate(ledger, batches, 1)


def test_only_uses_batches_of_the_requested_item():
    other_item = batch_key("B-OTHER", item_id="I002", expiry=date(2026, 7, 31))
    ledger, batches = stocked((other_item, 100), (NEW, 10))
    assert allocate(ledger, batches, 10) == [(NEW, 10)]


def test_refuses_when_sellable_stock_is_short_even_if_expired_stock_exists():
    ledger, batches = stocked((EXPIRED, 500), (NEW, 10))
    with pytest.raises(InsufficientStockError, match="only 10 are sellable"):
        allocate(ledger, batches, 11)


def test_breaks_expiry_ties_by_batch_number():
    first = batch_key("A100", expiry=date(2027, 3, 31))
    second = batch_key("B200", expiry=date(2027, 3, 31))
    ledger, batches = stocked((second, 5), (first, 5))
    assert allocate(ledger, batches, 7) == [(first, 5), (second, 2)]


def test_rejects_a_non_positive_quantity():
    ledger, batches = stocked((NEW, 10))
    with pytest.raises(ValueError, match="positive"):
        allocate(ledger, batches, 0)


def test_needs_a_record_for_every_batch_in_stock():
    ledger, _ = stocked((NEW, 10))
    with pytest.raises(LookupError, match="no batch record"):
        allocate(ledger, {}, 5)


def test_sellable_stock_lists_only_sellable_batches_in_fefo_order():
    ledger, batches = stocked((NEW, 7), (EXPIRED, 3), (OLD, 4))
    assert sellable_stock(ledger, batches, item_id="I001", location_id="GODOWN", on=TODAY) == [
        (OLD, 4),
        (NEW, 7),
    ]


def test_sellable_stock_leaves_out_batches_that_have_sold_out():
    ledger, batches = stocked((OLD, 4), (NEW, 7))
    ledger.append(movement(MovementType.SALE, -4, when=at(2), batch=OLD))
    assert sellable_stock(ledger, batches, item_id="I001", location_id="GODOWN", on=TODAY) == [
        (NEW, 7)
    ]
