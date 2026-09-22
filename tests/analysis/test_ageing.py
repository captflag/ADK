from datetime import date, datetime
from decimal import Decimal

import pytest

from batchward.analysis.ageing import age_bucket, dead_stock, stock_ageing
from batchward.core.clock import IST
from batchward.core.ledger import Ledger
from batchward.core.models import Location, MovementType
from factories import at, batch_key, movement

PURCHASE = MovementType.PURCHASE
SALE = MovementType.SALE
ADJUSTMENT = MovementType.ADJUSTMENT

GODOWN = Location(id="GODOWN", name="Main godown")
RETURNS = Location(id="RETURNS", name="Expiry shelf", sellable=False)
LOCATIONS = [GODOWN, RETURNS]


def day(n, month=1):
    return date(2026, month, n)


class TestAgeBucket:
    @pytest.mark.parametrize(
        ("days", "bucket"),
        [
            (0, "0-30 days"),
            (30, "0-30 days"),
            (31, "31-60 days"),
            (90, "61-90 days"),
            (91, "91-180 days"),
            (180, "91-180 days"),
            (181, "over 180 days"),
            (2000, "over 180 days"),
        ],
    )
    def test_places_each_age_in_its_bucket(self, days, bucket):
        assert age_bucket(days) == bucket

    def test_rejects_a_negative_age(self):
        with pytest.raises(ValueError, match="negative"):
            age_bucket(-1)


class TestStockAgeing:
    def test_ages_each_batch_from_its_first_arrival_oldest_first(self):
        old, new = batch_key("OLD"), batch_key("NEW")
        ledger = Ledger(
            [
                movement(PURCHASE, 10, when=at(1), batch=old),
                movement(PURCHASE, 5, when=at(20), batch=old),
                movement(PURCHASE, 8, when=at(25), batch=new),
            ]
        )
        costs = {old: Decimal("10.00")}
        ages = stock_ageing(ledger, costs, on=day(31))
        assert [(a.batch, a.units, a.received, a.age_days, a.bucket, a.value) for a in ages] == [
            (old, 15, day(1), 30, "0-30 days", Decimal("150.00")),
            (new, 8, day(25), 6, "0-30 days", None),
        ]

    def test_reflects_stock_as_it_stood_on_the_day(self):
        ledger = Ledger([movement(PURCHASE, 10, when=at(1)), movement(SALE, -10, when=at(15))])
        assert [a.units for a in stock_ageing(ledger, {}, on=day(10))] == [10]
        assert stock_ageing(ledger, {}, on=day(20)) == []

    def test_a_batch_without_a_purchase_arrives_with_its_first_added_stock(self):
        ledger = Ledger([movement(ADJUSTMENT, 6, when=at(3))])
        (age,) = stock_ageing(ledger, {}, on=day(10))
        assert age.received == day(3)

    def test_a_purchase_reversed_after_the_day_still_ages_the_stock_it_brought(self):
        purchase = movement(PURCHASE, 10, when=at(1))
        ledger = Ledger([purchase])
        ledger.reverse(purchase.id, reversal_id="R1", at=at(10), document_ref="CORR-1")
        (age,) = stock_ageing(ledger, {}, on=day(5))
        assert (age.units, age.received, age.age_days) == (10, day(1), 4)
        assert stock_ageing(ledger, {}, on=day(10)) == []

    def test_counts_a_movement_in_the_last_second_of_the_day(self):
        last_second = datetime(2026, 1, 5, 23, 59, 59, 500_000, tzinfo=IST)
        ledger = Ledger([movement(PURCHASE, 10, when=at(1)), movement(SALE, -10, when=last_second)])
        assert stock_ageing(ledger, {}, on=day(5)) == []


class TestDeadStock:
    def test_flags_an_item_that_has_not_sold_for_longer_than_the_limit(self):
        ledger = Ledger([movement(PURCHASE, 50, when=at(1)), movement(SALE, -5, when=at(2))])
        (dead,) = dead_stock(ledger, {batch_key(): Decimal("4.00")}, LOCATIONS, on=day(31, 5))
        assert (dead.item_id, dead.units, dead.value, dead.last_sold) == (
            "I001",
            45,
            Decimal("180.00"),
            day(2),
        )
        assert dead.idle_days == (day(31, 5) - day(2)).days

    def test_an_item_that_sold_recently_is_not_dead(self):
        ledger = Ledger([movement(PURCHASE, 50, when=at(1)), movement(SALE, -5, when=at(28))])
        assert dead_stock(ledger, {}, LOCATIONS, on=day(15, 2), idle_days=30) == []

    def test_a_new_item_gets_time_to_sell_from_when_it_arrived(self):
        ledger = Ledger([movement(PURCHASE, 50, when=at(20))])
        assert dead_stock(ledger, {}, LOCATIONS, on=day(31), idle_days=30) == []
        (dead,) = dead_stock(ledger, {}, LOCATIONS, on=day(28, 2), idle_days=30)
        assert (dead.last_sold, dead.idle_days) == (None, 39)

    def test_an_item_restocked_after_selling_out_long_ago_gets_time_to_sell_again(self):
        restocked = batch_key("AZ5001")
        ledger = Ledger(
            [
                movement(PURCHASE, 50, when=at(1)),
                movement(SALE, -50, when=at(2)),
                movement(PURCHASE, 50, when=at(1, month=6), batch=restocked),
            ]
        )
        assert dead_stock(ledger, {}, LOCATIONS, on=day(3, 6), idle_days=120) == []
        (dead,) = dead_stock(ledger, {}, LOCATIONS, on=day(1, 11), idle_days=120)
        assert (dead.last_sold, dead.idle_days) == (day(2), (day(1, 11) - day(1, 6)).days)

    def test_a_sale_reversed_after_the_day_still_counts_on_that_day(self):
        sale = movement(SALE, -5, when=at(20))
        ledger = Ledger([movement(PURCHASE, 50, when=at(1)), sale])
        ledger.reverse(sale.id, reversal_id="R1", at=at(1, month=3), document_ref="CORR-1")
        assert dead_stock(ledger, {}, LOCATIONS, on=day(25), idle_days=10) == []
        (dead,) = dead_stock(ledger, {}, LOCATIONS, on=day(10, 3), idle_days=10)
        assert (dead.last_sold, dead.idle_days) == (None, 68)

    def test_a_purchase_reversed_after_the_day_still_counts_on_that_day(self):
        purchase = movement(PURCHASE, 10, when=at(1))
        ledger = Ledger([purchase])
        ledger.reverse(purchase.id, reversal_id="R1", at=at(20), document_ref="CORR-1")
        (dead,) = dead_stock(ledger, {}, LOCATIONS, on=day(15), idle_days=10)
        assert (dead.units, dead.idle_days) == (10, 14)

    def test_ignores_stock_that_is_not_sellable(self):
        ledger = Ledger([movement(PURCHASE, 50, when=at(1), location_id="RETURNS")])
        assert dead_stock(ledger, {}, LOCATIONS, on=day(30, 6)) == []

    def test_counts_units_without_a_known_cost_separately(self):
        ledger = Ledger([movement(PURCHASE, 50, when=at(1))])
        (dead,) = dead_stock(ledger, {}, LOCATIONS, on=day(30, 6))
        assert (dead.value, dead.unvalued_units) == (Decimal("0.00"), 50)

    def test_largest_value_is_listed_first(self):
        cheap, dear = batch_key("C", item_id="CHEAP"), batch_key("D", item_id="DEAR")
        ledger = Ledger(
            [
                movement(PURCHASE, 100, when=at(1), batch=cheap),
                movement(PURCHASE, 2, when=at(1), batch=dear),
            ]
        )
        costs = {cheap: Decimal("1.00"), dear: Decimal("500.00")}
        assert [d.item_id for d in dead_stock(ledger, costs, LOCATIONS, on=day(30, 6))] == [
            "DEAR",
            "CHEAP",
        ]

    def test_refuses_stock_at_a_location_it_was_not_told_about(self):
        ledger = Ledger([movement(PURCHASE, 5, when=at(1), location_id="MYSTERY")])
        with pytest.raises(ValueError, match="MYSTERY"):
            dead_stock(ledger, {}, LOCATIONS, on=day(30, 6))


def test_dead_stock_checks_locations_as_they_stood_on_the_day_analysed():
    # Stock sat at MYSTERY in January and has since moved away; a February view is fine,
    # but a January view must not silently ignore it.
    ledger = Ledger(
        [
            movement(PURCHASE, 5, when=at(1), location_id="MYSTERY"),
            movement(MovementType.TRANSFER_OUT, -5, when=at(1, month=2), location_id="MYSTERY"),
            movement(MovementType.TRANSFER_IN, 5, when=at(1, month=2)),
        ]
    )
    with pytest.raises(ValueError, match="MYSTERY"):
        dead_stock(ledger, {}, LOCATIONS, on=day(20), idle_days=10)
    assert [d.units for d in dead_stock(ledger, {}, LOCATIONS, on=day(20, 2), idle_days=10)] == [5]
