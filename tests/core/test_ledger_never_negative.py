"""The ledger refuses any movement that would take a batch below zero (ADR 0001)."""

import pytest

from batchward.core.ledger import InsufficientStockError, Ledger
from batchward.core.models import MovementType
from factories import at, batch_key, movement

PURCHASE = MovementType.PURCHASE
SALE = MovementType.SALE
ADJUSTMENT = MovementType.ADJUSTMENT


def test_refuses_a_sale_larger_than_the_stock_on_hand():
    ledger = Ledger([movement(PURCHASE, 10, when=at(1))])
    with pytest.raises(InsufficientStockError, match="would leave -2 units"):
        ledger.append(movement(SALE, -12, when=at(2)))


def test_a_refused_movement_leaves_the_ledger_unchanged():
    ledger = Ledger([movement(PURCHASE, 10, when=at(1))])
    refused = movement(SALE, -12, when=at(2))
    with pytest.raises(InsufficientStockError):
        ledger.append(refused)
    assert len(ledger) == 1
    assert ledger.balance(batch_key(), "GODOWN") == 10
    ledger.append(movement(SALE, -10, when=at(3), movement_id=refused.id))


def test_allows_selling_the_last_unit():
    ledger = Ledger([movement(PURCHASE, 10, when=at(1))])
    ledger.append(movement(SALE, -10, when=at(2)))
    assert ledger.balance(batch_key(), "GODOWN") == 0


def test_refuses_a_backdated_sale_before_the_stock_arrived():
    ledger = Ledger([movement(PURCHASE, 10, when=at(5))])
    with pytest.raises(InsufficientStockError):
        ledger.append(movement(SALE, -4, when=at(3)))


def test_refuses_a_backdated_sale_that_starves_a_later_one():
    ledger = Ledger([movement(PURCHASE, 10, when=at(1)), movement(SALE, -8, when=at(5))])
    with pytest.raises(InsufficientStockError, match="would leave -3 units"):
        ledger.append(movement(SALE, -5, when=at(3)))


def test_accepts_a_backdated_sale_that_fits():
    ledger = Ledger([movement(PURCHASE, 10, when=at(1)), movement(SALE, -4, when=at(5))])
    ledger.append(movement(SALE, -6, when=at(3)))
    assert ledger.balance(batch_key(), "GODOWN") == 0


def test_always_accepts_stock_coming_in_even_backdated():
    ledger = Ledger([movement(PURCHASE, 10, when=at(5))])
    ledger.append(movement(PURCHASE, 3, when=at(1)))
    assert ledger.balance(batch_key(), "GODOWN") == 13


def test_backdated_sale_at_the_same_instant_follows_a_purchase_recorded_earlier():
    ledger = Ledger([movement(PURCHASE, 10, when=at(5)), movement(PURCHASE, 5, when=at(6))])
    ledger.append(movement(SALE, -10, when=at(5)))
    assert ledger.balance(batch_key(), "GODOWN", as_of=at(5)) == 0


def test_stock_at_another_location_does_not_count():
    ledger = Ledger([movement(PURCHASE, 10, when=at(1), location_id="COLD_ROOM")])
    with pytest.raises(InsufficientStockError):
        ledger.append(movement(SALE, -1, when=at(2)))


def test_refuses_a_negative_adjustment_below_zero():
    ledger = Ledger([movement(PURCHASE, 2, when=at(1))])
    with pytest.raises(InsufficientStockError):
        ledger.append(movement(ADJUSTMENT, -3, when=at(2)))
