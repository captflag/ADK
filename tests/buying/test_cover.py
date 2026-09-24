from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from batchward.analysis.classification import ValueClass, VariabilityClass
from batchward.buying.cover import (
    BY_CLASS,
    Cover,
    CoverTable,
    ItemClass,
    cells,
    classify,
    classify_items,
)
from batchward.buying.suggest import Policy, suggest
from batchward.core.clock import IST
from batchward.core.ledger import Ledger
from batchward.core.models import Item, Location, MovementType
from factories import batch_key, movement

ON = date(2026, 3, 1)
GODOWN = Location("GODOWN", "Godown")
AX = ItemClass(ValueClass.A, VariabilityClass.X)
CZ = ItemClass(ValueClass.C, VariabilityClass.Z)


def item(item_id):
    return Item(
        id=item_id,
        company_id="C01",
        brand=f"Brand {item_id}",
        molecule="Paracetamol",
        strength="500 mg",
        unit="strip of 10 tablets",
        hsn="3004",
        gst_rate=Decimal("0.05"),
        mrp=Decimal("30.00"),
    )


def test_one_cover_is_half_safety_and_half_per_order():
    assert Cover.flat(21) == Cover(10.5, 10.5)
    assert Cover.flat(21).days == 21
    assert str(Cover(7, 10.5)) == "7 + 10.5 days"
    with pytest.raises(ValueError, match="safety cannot be negative"):
        Cover(-1, 7)
    with pytest.raises(ValueError, match="must cover some days"):
        Cover(7, 0)


def test_safety_follows_variability_and_days_per_order_follow_value():
    assert BY_CLASS.cover(AX) == Cover(7, 7)
    assert BY_CLASS.cover(CZ) == Cover(10.5, 28)
    assert [str(cell) for cell in cells()] == [
        "AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ",
    ]  # fmt: skip
    assert str(BY_CLASS) == "safety days X 7, Y 10.5, Z 10.5; days per order A 7, B 10.5, C 28"
    with pytest.raises(ValueError, match="needs safety for X, Y and Z"):
        CoverTable({VariabilityClass.X: 7}, dict.fromkeys(ValueClass, 7))


def test_items_are_classed_by_the_money_they_carry_and_how_steady_they_sell():
    values = {"BIG": Decimal(900), "MID": Decimal(80), "SMALL": Decimal(20)}
    weekly = {"BIG": [10, 11, 9, 10], "MID": [0, 30, 0, 2], "SMALL": [2, 9, 1, 8]}
    classes = classify(values, weekly, ["BIG", "MID", "SMALL", "NEVER"])
    assert {item_id: str(c) for item_id, c in classes.items()} == {
        "BIG": "AX",
        "MID": "BZ",
        "SMALL": "CY",
        "NEVER": "CZ",
    }


def steady_ledger(days=182, per_day=5):
    """One item bought at ₹10 and sold at the same pace every day before ON."""
    key = batch_key(item_id="I001", expiry=date(2027, 12, 31))
    bought = replace(
        movement(MovementType.PURCHASE, per_day * days * 2, when=_day(ON - timedelta(days + 1)),
                 batch=key),
        rate=Decimal("10.00"),
    )  # fmt: skip
    sold = [
        movement(MovementType.SALE, -per_day, when=_day(ON - timedelta(n)), batch=key)
        for n in range(1, days + 1)
    ]
    return Ledger([bought, *sold])


def _day(day):
    from datetime import datetime

    return datetime(day.year, day.month, day.day, 11, tzinfo=IST)


def test_an_item_is_classed_from_the_sales_before_the_day():
    ledger = steady_ledger()
    assert classify_items(ledger, on=ON, items=["I001", "I002"]) == {"I001": AX, "I002": CZ}


def test_covering_by_class_orders_each_item_to_its_class_and_says_which():
    ledger = steady_ledger()
    rates = {"I001": 5.0}
    items = {"I001": item("I001")}
    (by_class,) = suggest(ledger, items, [GODOWN], rates, on=ON)
    # AX: order below 5 x (4 + 7) = 55, up to 5 x (4 + 7 + 7) = 90.
    assert (by_class.item_class, by_class.cover) == (AX, Cover(7, 7))
    assert (by_class.reorder_point, by_class.order_up_to) == (55, 90)
    (flat,) = suggest(ledger, items, [GODOWN], rates, on=ON, policy=Policy(cover_days=21))
    assert (flat.item_class, flat.cover) == (None, Cover(10.5, 10.5))
    assert (flat.reorder_point, flat.order_up_to) == (73, 125)
    (given,) = suggest(ledger, items, [GODOWN], rates, on=ON, classes={"I001": CZ})
    assert (given.reorder_point, given.order_up_to) == (73, 213)


def test_a_policy_covers_by_class_unless_it_names_one_cover():
    assert Policy().by_class and not Policy(21).by_class
    assert str(Policy()) == "4 days' lead time and each item's cover by its class"
    assert str(Policy(21, 2)) == "2 days' lead time and 21 days of demand"
    assert Policy(21).cover(AX) == Cover(10.5, 10.5)
    with pytest.raises(ValueError, match="needs the item's class"):
        Policy().cover(None)
    with pytest.raises(ValueError, match="at least a day"):
        Policy(cover_days=0)
