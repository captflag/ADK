from dataclasses import replace
from decimal import Decimal

from batchward.analysis.costs import batch_costs, value_at_cost
from batchward.core.ledger import Ledger
from batchward.core.models import MovementType
from factories import at, batch_key, movement

PURCHASE = MovementType.PURCHASE


def bought(qty, rate, day=1, batch=None):
    return replace(movement(PURCHASE, qty, when=at(day), batch=batch), rate=Decimal(rate))


def test_cost_is_the_quantity_weighted_average_purchase_rate():
    ledger = Ledger([bought(100, "60.00"), bought(50, "66.00", day=2)])
    assert batch_costs(ledger) == {batch_key(): Decimal("62.00")}


def test_costs_are_kept_per_batch():
    other = batch_key("NP1102")
    ledger = Ledger([bought(10, "40.00"), bought(10, "12.50", batch=other)])
    assert batch_costs(ledger) == {batch_key(): Decimal("40.00"), other: Decimal("12.50")}


def test_a_reversed_purchase_does_not_affect_cost():
    wrong = bought(100, "99.00")
    ledger = Ledger([bought(20, "50.00"), wrong])
    ledger.reverse(wrong.id, reversal_id="R1", at=at(3), document_ref="CORR-1")
    assert batch_costs(ledger) == {batch_key(): Decimal("50.00")}


def test_a_batch_bought_without_a_rate_has_no_cost():
    ledger = Ledger([movement(PURCHASE, 10, when=at(1))])
    assert batch_costs(ledger) == {}


def test_cost_is_rounded_to_the_paisa():
    ledger = Ledger([bought(3, "10.00"), bought(3, "10.01", day=2), bought(3, "10.01", day=3)])
    assert batch_costs(ledger)[batch_key()] == Decimal("10.01")


def test_value_at_cost_multiplies_units_by_cost_and_admits_unknown_cost():
    assert value_at_cost(12, Decimal("62.50")) == Decimal("750.00")
    assert value_at_cost(12, None) is None
