from datetime import date
from decimal import Decimal

import pytest

from batchward.analysis.expiry import RiskReason, expiry_exposure
from batchward.core.ledger import Ledger
from batchward.core.models import Location, MovementType
from factories import at, batch_key, movement

PURCHASE = MovementType.PURCHASE
GODOWN = Location(id="GODOWN", name="Main godown")
RETURNS = Location(id="RETURNS", name="Expiry shelf", sellable=False)
LOCATIONS = [GODOWN, RETURNS]
ON = date(2026, 1, 1)


def stock(*batches):
    """A ledger holding (key, units, location) entries bought before ON."""
    return Ledger(
        movement(PURCHASE, units, when=at(1, month=12, year=2025), batch=key, location_id=loc)
        for key, units, loc in batches
    )


def exposure(ledger, rates, costs=None, **kwargs):
    return expiry_exposure(ledger, costs or {}, LOCATIONS, rates, on=ON, **kwargs)


def test_stock_that_sells_before_it_expires_is_not_at_risk():
    ledger = stock((batch_key(expiry=date(2026, 3, 31)), 50, "GODOWN"))
    assert exposure(ledger, {"I001": 2.0}) == []  # 89 days x 2 a day covers 50


def test_the_part_that_cannot_sell_in_time_is_at_risk():
    key = batch_key(expiry=date(2026, 1, 31))
    ledger = stock((key, 100, "GODOWN"))
    (risk,) = exposure(ledger, {"I001": 2.0}, costs={key: Decimal("10.00")})
    # 30 sellable days x 2 a day = 60 sold, 40 left.
    assert (risk.days_to_expiry, risk.expected_to_sell, risk.units_at_risk) == (30, 60, 40)
    assert risk.value_at_risk == Decimal("400.00")
    assert risk.reason is RiskReason.WILL_NOT_SELL_IN_TIME


def test_a_later_batch_only_sells_what_earlier_batches_leave_it():
    first = batch_key("FIRST", expiry=date(2026, 1, 31))
    second = batch_key("SECOND", expiry=date(2026, 3, 2))
    ledger = stock((second, 40, "GODOWN"), (first, 50, "GODOWN"))
    risks = {r.batch.batch_no: r for r in exposure(ledger, {"I001": 1.0})}
    # First: 30 days sell 30 of 50, 20 at risk. Second: 60 days of demand, 30 already
    # taken by the first batch, so 30 of 40 sell and 10 are at risk.
    assert (risks["FIRST"].expected_to_sell, risks["FIRST"].units_at_risk) == (30, 20)
    assert (risks["SECOND"].expected_to_sell, risks["SECOND"].units_at_risk) == (30, 10)


def test_stock_on_an_unsellable_shelf_is_at_risk_in_full():
    ledger = stock((batch_key(expiry=date(2027, 1, 31)), 12, "RETURNS"))
    (risk,) = exposure(ledger, {"I001": 50.0})
    assert (risk.units_at_risk, risk.reason) == (12, RiskReason.UNSELLABLE_SHELF)


def test_expired_stock_still_on_hand_is_at_risk_in_full():
    ledger = stock((batch_key(expiry=date(2025, 12, 31)), 7, "GODOWN"))
    (risk,) = exposure(ledger, {"I001": 50.0})
    assert (risk.days_to_expiry, risk.units_at_risk, risk.reason) == (
        -1,
        7,
        RiskReason.ALREADY_EXPIRED,
    )


def test_stock_is_not_sold_on_its_expiry_date():
    # Expires tomorrow: one sellable day.
    ledger = stock((batch_key(expiry=date(2026, 1, 2)), 5, "GODOWN"))
    (risk,) = exposure(ledger, {"I001": 3.0})
    assert (risk.expected_to_sell, risk.units_at_risk) == (3, 2)


def test_an_item_without_a_forecast_is_treated_as_not_selling():
    ledger = stock((batch_key(expiry=date(2026, 6, 30)), 9, "GODOWN"))
    (risk,) = exposure(ledger, {})
    assert risk.units_at_risk == 9


def test_within_days_limits_the_report_to_batches_expiring_soon():
    soon = batch_key("SOON", expiry=date(2026, 2, 28))
    later = batch_key("LATER", item_id="I002", expiry=date(2026, 12, 31))
    ledger = stock((soon, 10, "GODOWN"), (later, 10, "GODOWN"))
    assert [r.batch for r in exposure(ledger, {}, within_days=90)] == [soon]


def test_largest_value_at_risk_comes_first_and_unknown_cost_is_admitted():
    cheap = batch_key("CHEAP", expiry=date(2026, 2, 28))
    dear = batch_key("DEAR", item_id="I002", expiry=date(2026, 2, 28))
    unknown = batch_key("UNKNOWN", item_id="I003", expiry=date(2026, 2, 28))
    ledger = stock((cheap, 10, "GODOWN"), (dear, 10, "GODOWN"), (unknown, 10, "GODOWN"))
    costs = {cheap: Decimal("1.00"), dear: Decimal("90.00")}
    risks = exposure(ledger, {}, costs=costs)
    assert [r.batch.batch_no for r in risks] == ["DEAR", "CHEAP", "UNKNOWN"]
    assert risks[-1].value_at_risk is None


def test_refuses_stock_at_a_location_it_was_not_told_about():
    ledger = stock((batch_key(), 5, "MYSTERY"))
    with pytest.raises(ValueError, match="MYSTERY"):
        exposure(ledger, {})
