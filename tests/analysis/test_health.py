from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from batchward.analysis.classification import ValueClass
from batchward.analysis.health import stock_health
from batchward.core.ledger import Ledger
from batchward.core.models import Location, MovementType
from batchward.sim.business import SimConfig, simulate
from factories import at, batch_key, movement

GODOWN = Location(id="GODOWN", name="Main godown")
RETURNS = Location(id="RETURNS", name="Expiry shelf", sellable=False)


def bought(qty, rate, *, batch, day=1, month=1, location_id="GODOWN"):
    purchase = movement(
        MovementType.PURCHASE, qty, when=at(day, month=month), batch=batch, location_id=location_id
    )
    return replace(purchase, rate=Decimal(rate))


class TestOnAHandBuiltLedger:
    @pytest.fixture
    def health(self):
        selling = batch_key("SELL", item_id="FAST", expiry=date(2027, 12, 31))
        idle = batch_key("IDLE", item_id="SLOW", expiry=date(2026, 7, 31))
        returned = batch_key("RET", item_id="FAST", expiry=date(2026, 6, 30))
        sales = [
            movement(MovementType.SALE, -3, when=at(d, month=m), batch=selling)
            for m in (2, 3, 4)
            for d in range(1, 28)
        ]
        ledger = Ledger(
            [
                bought(500, "20.00", batch=selling),
                bought(40, "50.00", batch=idle),
                bought(10, "20.00", batch=returned, location_id="RETURNS"),
                *sales,
            ]
        )
        # SLOW arrived on 1 January and never sold: idle for 121 days by 2 May.
        return stock_health(ledger, [GODOWN, RETURNS], on=date(2026, 5, 2))

    def test_values_all_stock_at_cost_by_age(self, health):
        # 500 - 243 sold = 257 x 20 + 40 x 50 + 10 x 20 = 5,140 + 2,000 + 200.
        assert health.stock_value == Decimal("7340.00")
        assert sum(health.value_by_age.values()) == health.stock_value
        assert health.value_by_age["91-180 days"] == health.stock_value

    def test_finds_the_item_that_stopped_selling(self, health):
        assert [d.item_id for d in health.dead_stock] == ["SLOW"]
        assert health.dead_stock_value == Decimal("2000.00")

    def test_counts_unsellable_and_unsaleable_stock_at_risk_of_expiry(self, health):
        at_risk = {r.batch.batch_no: r.units_at_risk for r in health.expiry_risks}
        assert at_risk == {"IDLE": 40, "RET": 10}
        assert health.expiry_value_at_risk == Decimal("2200.00")

    def test_classifies_items_by_what_they_consumed(self, health):
        assert health.items_by_class == {ValueClass.A: 1, ValueClass.B: 0, ValueClass.C: 0}
        assert health.consumption_by_class[ValueClass.A] == Decimal("4860.00")


def test_a_past_day_is_valued_at_what_its_stock_had_cost_by_then():
    key = batch_key()
    ledger = Ledger([bought(10, "10.00", batch=key), bought(10, "30.00", batch=key, day=20)])
    assert stock_health(ledger, [GODOWN], on=date(2026, 1, 10)).stock_value == Decimal("100.00")


def test_a_reversal_after_the_day_does_not_change_the_report_for_that_day():
    purchase = bought(10, "10.00", batch=batch_key())
    ledger = Ledger([purchase])
    ledger.reverse(purchase.id, reversal_id="R1", at=at(10), document_ref="CORR-1")
    assert stock_health(ledger, [GODOWN], on=date(2026, 1, 5)).stock_value == Decimal("100.00")


def test_on_a_simulated_stockist_every_total_agrees_with_its_parts():
    start = date(2025, 9, 1)
    business = simulate(SimConfig(start=start, days=200, n_chemists=80))
    health = stock_health(business.ledger, business.locations, on=start + timedelta(days=200))

    assert health.stock_value > 0
    assert sum(health.value_by_age.values()) == health.stock_value
    assert health.dead_stock_value == sum(d.value for d in health.dead_stock)
    assert all(r.units_at_risk > 0 for r in health.expiry_risks)
    assert all(r.days_to_expiry <= 180 for r in health.expiry_risks)
    assert sum(health.items_by_class.values()) > 0
