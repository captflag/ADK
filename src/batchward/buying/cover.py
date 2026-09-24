"""How much stock each item is ordered to hold, by its ABC-XYZ class (ADR 0022).

An item's cover has two parts, both in days of its forecast demand:

- **safety**, held against demand running ahead of the forecast while an order
  is on its way. It follows how predictable the item is: its XYZ class.
- **cycle**, what each order brings in. It follows how much money the item
  carries: its ABC class. Items that carry the money are ordered often, in
  small amounts, so little of it sits on the shelf; cheap items are ordered
  seldom, in larger amounts, which saves order lines for a few rupees of stock.

An order is placed when the stock that will be there to sell falls below the
lead time and the safety, and it tops stock up to the lead time, the safety and
the cycle. One cover for every item is the special case of equal parts: 21
days of cover is 10.5 days of safety and 10.5 days per order, the simulated
owner's own habit and Batchward's rule before this one (ADR 0020).

The days in ``BY_CLASS`` were chosen by replaying two simulated years of demand
(``batchward cover-backtest``): the least stock that fills as many of the units
chemists order as the one-cover rule, with no more order lines. Steady items
need less safety than the rule held, the items that carry the money are best
ordered weekly, and the cheap ones monthly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from batchward.analysis.classification import (
    ValueClass,
    VariabilityClass,
    classify_abc,
    classify_xyz,
    consumption_value,
)
from batchward.analysis.costs import batch_costs
from batchward.analysis.demand import aggregate, daily_sales
from batchward.core.clock import end_of_day
from batchward.core.ledger import Ledger

VALUE_DAYS = 365
"""Days of consumption an item's ABC class is judged on."""
VARIABILITY_WEEKS = 26
"""Weeks of sales an item's XYZ class is judged on: the forecast's own history."""


@dataclass(frozen=True, slots=True, order=True)
class Cover:
    safety_days: float
    cycle_days: float

    def __post_init__(self) -> None:
        if self.safety_days < 0 or self.cycle_days <= 0:
            raise ValueError("safety cannot be negative, and an order must cover some days")

    @classmethod
    def flat(cls, cover_days: float) -> Cover:
        """One cover for every item, as ADR 0020 ordered: half safety, half per order."""
        return cls(cover_days / 2, cover_days / 2)

    @property
    def days(self) -> float:
        return self.safety_days + self.cycle_days

    def __str__(self) -> str:
        return f"{_days(self.safety_days)} + {_days(self.cycle_days)} days"


@dataclass(frozen=True, slots=True, order=True)
class ItemClass:
    value: ValueClass
    variability: VariabilityClass

    def __str__(self) -> str:
        return f"{self.value}{self.variability}"


def cells() -> list[ItemClass]:
    """Every class, AX to CZ."""
    return [ItemClass(value, varies) for value in ValueClass for varies in VariabilityClass]


@dataclass(frozen=True, slots=True)
class CoverTable:
    """Safety by XYZ class and days per order by ABC class."""

    safety_days: Mapping[VariabilityClass, float]
    cycle_days: Mapping[ValueClass, float]

    def __post_init__(self) -> None:
        if set(self.safety_days) != set(VariabilityClass) or set(self.cycle_days) != set(
            ValueClass
        ):
            raise ValueError("a cover table needs safety for X, Y and Z and cycles for A, B and C")
        for cell in cells():
            self.cover(cell)

    def cover(self, item_class: ItemClass) -> Cover:
        return Cover(self.safety_days[item_class.variability], self.cycle_days[item_class.value])

    def __str__(self) -> str:
        safety = ", ".join(f"{c} {_days(self.safety_days[c])}" for c in VariabilityClass)
        cycle = ", ".join(f"{c} {_days(self.cycle_days[c])}" for c in ValueClass)
        return f"safety days {safety}; days per order {cycle}"


BY_CLASS = CoverTable(
    safety_days={VariabilityClass.X: 7, VariabilityClass.Y: 10.5, VariabilityClass.Z: 10.5},
    cycle_days={ValueClass.A: 7, ValueClass.B: 10.5, ValueClass.C: 28},
)
"""The covers Batchward orders to, from the replay recorded in ADR 0022."""


def classify(
    values: Mapping[str, Decimal], weekly: Mapping[str, Sequence[int]], items: Iterable[str]
) -> dict[str, ItemClass]:
    """Each item's class from its consumption value and its weekly demand.

    An item with no consumption value is C, and one with no demand is Z.
    """
    abc = classify_abc(values)
    return {
        item_id: ItemClass(
            abc.get(item_id, ValueClass.C),
            classify_xyz(weekly.get(item_id, ())).variability_class,
        )
        for item_id in items
    }


def classify_items(ledger: Ledger, *, on: date, items: Iterable[str]) -> dict[str, ItemClass]:
    """Each item's class on a day, from the sales before it: a year of value, 26 weeks of demand."""
    end = on - timedelta(days=1)
    as_of = end_of_day(end)
    values = consumption_value(
        ledger,
        batch_costs(ledger, as_of=as_of),
        start=on - timedelta(days=VALUE_DAYS),
        end=end,
        as_of=as_of,
    )
    history = daily_sales(
        ledger, start=on - timedelta(days=VARIABILITY_WEEKS * 7), end=end, as_of=as_of
    )
    weekly = {item_id: aggregate(series, 7) for item_id, series in history.items()}
    return classify(values, weekly, items)


def _days(days: float) -> str:
    return f"{days:g}"
