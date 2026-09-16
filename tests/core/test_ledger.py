import pytest

from batchward.core.ledger import DuplicateMovementError, Ledger, UnknownMovementError
from batchward.core.models import MovementType
from factories import at, batch_key, movement

PURCHASE = MovementType.PURCHASE
SALE = MovementType.SALE
TRANSFER_IN = MovementType.TRANSFER_IN
TRANSFER_OUT = MovementType.TRANSFER_OUT


def test_unknown_position_has_zero_balance():
    assert Ledger().balance(batch_key(), "GODOWN") == 0


def test_balance_is_the_sum_of_movements():
    ledger = Ledger([movement(PURCHASE, 100, when=at(1)), movement(SALE, -30, when=at(2))])
    assert ledger.balance(batch_key(), "GODOWN") == 70


def test_balances_are_kept_per_location():
    ledger = Ledger(
        [
            movement(PURCHASE, 100, when=at(1)),
            movement(TRANSFER_OUT, -40, when=at(2)),
            movement(TRANSFER_IN, 40, when=at(2), location_id="COLD_ROOM"),
        ]
    )
    assert ledger.balance(batch_key(), "GODOWN") == 60
    assert ledger.balance(batch_key(), "COLD_ROOM") == 40


def test_balance_as_of_ignores_later_movements():
    ledger = Ledger([movement(PURCHASE, 100, when=at(1)), movement(SALE, -30, when=at(5))])
    assert ledger.balance(batch_key(), "GODOWN", as_of=at(4)) == 100
    assert ledger.balance(batch_key(), "GODOWN", as_of=at(5)) == 70


def test_balances_as_of_ignores_later_movements():
    ledger = Ledger([movement(PURCHASE, 100, when=at(1)), movement(SALE, -30, when=at(5))])
    assert ledger.balances(as_of=at(4)) == {(batch_key(), "GODOWN"): 100}


def test_balances_omit_positions_that_are_empty():
    other = batch_key("NP1102")
    ledger = Ledger(
        [
            movement(PURCHASE, 10, when=at(1)),
            movement(SALE, -10, when=at(2)),
            movement(PURCHASE, 5, when=at(1), batch=other),
        ]
    )
    assert ledger.balances() == {(other, "GODOWN"): 5}


def test_rejects_a_movement_id_that_is_already_recorded():
    ledger = Ledger([movement(PURCHASE, 10, movement_id="M-DUP")])
    with pytest.raises(DuplicateMovementError):
        ledger.append(movement(PURCHASE, 10, movement_id="M-DUP"))


def test_get_returns_a_recorded_movement():
    recorded = movement(PURCHASE, 10)
    assert Ledger([recorded]).get(recorded.id) is recorded


def test_get_raises_for_an_unknown_movement():
    with pytest.raises(UnknownMovementError):
        Ledger().get("M-MISSING")


def test_iterates_in_append_order():
    late, early = movement(PURCHASE, 10, when=at(9)), movement(PURCHASE, 5, when=at(1))
    assert list(Ledger([late, early])) == [late, early]


def test_movements_for_a_batch_span_locations_oldest_first():
    first = movement(PURCHASE, 50, when=at(1))
    moved_out = movement(TRANSFER_OUT, -20, when=at(3))
    moved_in = movement(TRANSFER_IN, 20, when=at(3), location_id="COLD_ROOM")
    unrelated = movement(PURCHASE, 5, when=at(2), batch=batch_key("NP1102"))
    ledger = Ledger([moved_in, unrelated, first, moved_out])
    assert ledger.movements_for(batch_key()) == [first, moved_in, moved_out]


def test_stock_of_item_lists_only_batches_holding_stock():
    old, current = batch_key("OLD1"), batch_key("CUR1")
    ledger = Ledger(
        [
            movement(PURCHASE, 10, when=at(1), batch=old),
            movement(SALE, -10, when=at(2), batch=old),
            movement(PURCHASE, 7, when=at(3), batch=current),
        ]
    )
    assert ledger.stock_of_item("I001", "GODOWN") == {current: 7}


def test_a_sold_out_batch_is_listed_again_when_stock_comes_back():
    key = batch_key()
    sale = movement(SALE, -10, when=at(2), party_id="CHEM-1")
    ledger = Ledger([movement(PURCHASE, 10, when=at(1)), sale])
    ledger.append(movement(MovementType.SALE_RETURN, 3, when=at(3), party_id="CHEM-1"))
    assert ledger.stock_of_item("I001", "GODOWN") == {key: 3}
    ledger.reverse(sale.id, reversal_id="R1", at=at(4), document_ref="CORR-1")
    assert ledger.stock_of_item("I001", "GODOWN") == {key: 13}


def test_stock_of_item_ignores_other_items_and_locations():
    ledger = Ledger(
        [
            movement(PURCHASE, 4, when=at(1)),
            movement(PURCHASE, 5, when=at(1), batch=batch_key("X1", item_id="I002")),
            movement(PURCHASE, 6, when=at(1), batch=batch_key("X2"), location_id="COLD_ROOM"),
        ]
    )
    assert ledger.stock_of_item("I001", "GODOWN") == {batch_key(): 4}
