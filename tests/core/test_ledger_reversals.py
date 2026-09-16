"""Corrections are reversing entries, never edits (ADR 0001)."""

import pytest

from batchward.core.ledger import (
    InsufficientStockError,
    InvalidReversalError,
    Ledger,
    UnknownMovementError,
)
from batchward.core.models import MovementType
from factories import at, batch_key, movement

PURCHASE = MovementType.PURCHASE
SALE = MovementType.SALE
REVERSAL = MovementType.REVERSAL


def reverse(ledger, movement_id, *, day=10, reversal_id="R1"):
    return ledger.reverse(movement_id, reversal_id=reversal_id, at=at(day), document_ref="CORR-1")


def test_reversing_a_purchase_removes_its_stock_but_keeps_the_history():
    purchase = movement(PURCHASE, 40, when=at(1))
    ledger = Ledger([purchase])
    reverse(ledger, purchase.id)
    assert ledger.balance(batch_key(), "GODOWN") == 0
    assert len(ledger) == 2
    assert ledger.balance(batch_key(), "GODOWN", as_of=at(5)) == 40


def test_reversal_mirrors_the_original():
    sale = movement(SALE, -6, when=at(2), party_id="CHEM-7")
    ledger = Ledger([movement(PURCHASE, 10, when=at(1)), sale])
    reversal = reverse(ledger, sale.id)
    assert reversal.kind is REVERSAL
    assert reversal.reverses == sale.id
    assert reversal.qty == 6
    assert (reversal.batch, reversal.location_id, reversal.party_id) == (
        sale.batch,
        sale.location_id,
        sale.party_id,
    )
    assert ledger.balance(batch_key(), "GODOWN") == 10


def test_marks_the_original_as_reversed():
    purchase = movement(PURCHASE, 10, when=at(1))
    ledger = Ledger([purchase])
    assert not ledger.is_reversed(purchase.id)
    reverse(ledger, purchase.id)
    assert ledger.is_reversed(purchase.id)


def test_refuses_to_reverse_the_same_movement_twice():
    purchase = movement(PURCHASE, 10, when=at(1))
    ledger = Ledger([purchase])
    reverse(ledger, purchase.id, reversal_id="R1")
    with pytest.raises(InvalidReversalError, match="already reversed by R1"):
        reverse(ledger, purchase.id, reversal_id="R2")


def test_refuses_to_reverse_a_reversal():
    purchase = movement(PURCHASE, 10, when=at(1))
    ledger = Ledger([purchase])
    reversal = reverse(ledger, purchase.id)
    with pytest.raises(InvalidReversalError, match="itself a reversal"):
        reverse(ledger, reversal.id, day=11, reversal_id="R2")


def test_refuses_to_reverse_an_unknown_movement():
    with pytest.raises(UnknownMovementError):
        reverse(Ledger(), "M-MISSING")


def test_refuses_a_reversal_dated_before_the_original():
    purchase = movement(PURCHASE, 10, when=at(5))
    ledger = Ledger([purchase])
    with pytest.raises(InvalidReversalError, match="before the original"):
        reverse(ledger, purchase.id, day=4)


def test_refuses_to_reverse_a_purchase_whose_stock_is_already_sold():
    purchase = movement(PURCHASE, 10, when=at(1))
    ledger = Ledger([purchase, movement(SALE, -7, when=at(2))])
    with pytest.raises(InsufficientStockError):
        reverse(ledger, purchase.id)


def test_refuses_a_hand_built_reversal_that_does_not_cancel_exactly():
    purchase = movement(PURCHASE, 10, when=at(1))
    ledger = Ledger([purchase])
    with pytest.raises(InvalidReversalError, match="exactly cancel"):
        ledger.append(movement(REVERSAL, -4, when=at(2), reverses=purchase.id))


def test_refuses_a_hand_built_reversal_at_another_location():
    purchase = movement(PURCHASE, 10, when=at(1))
    ledger = Ledger([purchase, movement(PURCHASE, 10, when=at(1), location_id="COLD_ROOM")])
    with pytest.raises(InvalidReversalError, match="same batch and location"):
        ledger.append(
            movement(REVERSAL, -10, when=at(2), location_id="COLD_ROOM", reverses=purchase.id)
        )
