"""What to order: each item topped up to cover its forecast demand (ADR 0020, 0022).

An item's stock position is what will actually be there to sell: units on
sellable shelves, less what the forecast says will expire before it sells and
anything under a hold, plus what is still due on orders already placed. When
the position falls below the demand expected over the lead time and the
item's safety days, the item is ordered up to the demand over the lead time,
the safety and the days each order covers:

    reorder point = rate x (lead days + safety days)
    order up to   = rate x (lead days + safety days + cycle days)
    order         = order up to - position, when position < reorder point

Safety and cycle days come from the item's ABC-XYZ class (ADR 0022), or from
one cover for every item when a policy names one: 21 days is 10.5 of each.
The rate is the routed weekly forecast (ADR 0007) per day. An item that has not
sold is never ordered. An order is rounded up to whole cases when the item's
case size is on record (ADR 0023), and is in units otherwise.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from batchward.analysis.costs import PAISA
from batchward.analysis.expiry import expiry_exposure
from batchward.buying.cases import SLOW_CASE_DAYS, CaseTable, to_cases
from batchward.buying.cover import BY_CLASS, Cover, CoverTable, ItemClass, classify_items
from batchward.core.clock import end_of_day
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, Item, Location, MovementType

COVER_DAYS = 21
"""Days of demand an order tops stock up to, beyond the lead time, under one cover for all."""
LEAD_DAYS = 4
"""Days from placing an order to the stock arriving."""


@dataclass(frozen=True, slots=True)
class Policy:
    cover_days: int | None = None
    """One cover for every item, in days of demand beyond the lead time; None gives each
    item the cover of its class from ``table``."""
    lead_days: int = LEAD_DAYS
    table: CoverTable = BY_CLASS

    def __post_init__(self) -> None:
        if (self.cover_days is not None and self.cover_days < 1) or self.lead_days < 0:
            raise ValueError("cover must be at least a day, and lead time cannot be negative")

    @property
    def by_class(self) -> bool:
        return self.cover_days is None

    def cover(self, item_class: ItemClass | None) -> Cover:
        if self.cover_days is not None:
            return Cover.flat(self.cover_days)
        if item_class is None:
            raise ValueError("covering by class needs the item's class")
        return self.table.cover(item_class)

    def __str__(self) -> str:
        cover = (
            "each item's cover by its class"
            if self.by_class
            else f"{self.cover_days} days of demand"
        )
        return f"{self.lead_days} days' lead time and {cover}"


@dataclass(frozen=True, slots=True)
class Suggestion:
    item: Item
    daily_rate: float
    usable: int
    """Units on sellable shelves that will sell before expiry and are not held."""
    due: int
    """Units still due on orders already placed."""
    reorder_point: int
    order_up_to: int
    quantity: int
    """Units to order; 0 when the position is enough."""
    rate: Decimal | None
    """The last rate the item was bought at, to value the order."""
    cover: Cover
    item_class: ItemClass | None = None
    """The item's ABC-XYZ class, when its cover came from it."""
    case_units: int | None = None
    """Units in the item's case, when on record: the order is in whole cases of it."""
    needed: int = 0
    """Units the cover asked for, before rounding to whole cases."""

    @property
    def position(self) -> int:
        return self.usable + self.due

    @property
    def value(self) -> Decimal | None:
        return None if self.rate is None else (self.rate * self.quantity).quantize(PAISA)

    @property
    def cases(self) -> int | None:
        return None if self.case_units is None else self.quantity // self.case_units

    @property
    def case_days(self) -> float | None:
        """How many days of forecast demand one case holds."""
        if self.case_units is None or self.daily_rate <= 0:
            return None
        return self.case_units / self.daily_rate

    @property
    def slow_case(self) -> bool:
        """Whether one case holds more than a quarter's demand, and may not sell in time."""
        return self.quantity > 0 and (self.case_days or 0) > SLOW_CASE_DAYS

    @property
    def days_of_cover(self) -> float | None:
        """How many days the position lasts at the forecast rate."""
        return None if self.daily_rate <= 0 else self.position / self.daily_rate


def last_rates(ledger: Ledger) -> dict[str, Decimal]:
    """The rate each item was last bought at."""
    latest: dict[str, tuple] = {}
    for movement in ledger:
        if movement.kind is not MovementType.PURCHASE or movement.rate is None:
            continue
        if ledger.is_reversed(movement.id):
            continue
        item_id = movement.batch.item_id
        if item_id not in latest or movement.at >= latest[item_id][0]:
            latest[item_id] = (movement.at, movement.rate)
    return {item_id: rate for item_id, (_, rate) in latest.items()}


def suggest(
    ledger: Ledger,
    items: Mapping[str, Item],
    locations: Iterable[Location],
    daily_rates: Mapping[str, float],
    *,
    on: date,
    due: Mapping[str, int] | None = None,
    held: Iterable[BatchKey] = (),
    policy: Policy | None = None,
    classes: Mapping[str, ItemClass] | None = None,
    cases: CaseTable | None = None,
) -> list[Suggestion]:
    """A suggestion for every item that sells, those to order first, by value.

    Covering by class, each item's class is worked out from the ledger unless
    ``classes`` gives them. An item with a case size in ``cases`` on the day is
    ordered in whole cases, rounded up.
    """
    policy = policy or Policy()
    if policy.by_class and classes is None:
        classes = classify_items(ledger, on=on, items=daily_rates)
    locations = tuple(locations)
    sellable = {location.id for location in locations if location.sellable}
    held = set(held)
    usable: defaultdict[str, int] = defaultdict(int)
    for (key, location_id), units in ledger.balances(end_of_day(on)).items():
        if location_id in sellable and key not in held and key.expiry > on and units > 0:
            usable[key.item_id] += units
    for risk in expiry_exposure(ledger, {}, locations, daily_rates, on=on):
        if risk.location_id in sellable and risk.batch not in held and risk.days_to_expiry > 0:
            usable[risk.batch.item_id] -= risk.units_at_risk
    rates = last_rates(ledger)

    suggestions = []
    for item_id, daily in daily_rates.items():
        item = items.get(item_id)
        if item is None or daily <= 0:
            continue
        item_class = classes.get(item_id) if policy.by_class and classes is not None else None
        cover = policy.cover(item_class)
        reorder_point = math.ceil(daily * (policy.lead_days + cover.safety_days))
        order_up_to = math.ceil(daily * (policy.lead_days + cover.days))
        on_hand = max(0, usable[item_id])
        coming = (due or {}).get(item_id, 0)
        position = on_hand + coming
        needed = order_up_to - position if position < reorder_point else 0
        size = None if cases is None else cases.in_force(item_id, on)
        case_units = None if size is None else size.units
        quantity = needed if case_units is None else to_cases(needed, case_units)
        suggestions.append(
            Suggestion(
                item=item,
                daily_rate=daily,
                usable=on_hand,
                due=coming,
                reorder_point=reorder_point,
                order_up_to=order_up_to,
                quantity=quantity,
                rate=rates.get(item_id),
                cover=cover,
                item_class=item_class,
                case_units=case_units,
                needed=needed,
            )
        )
    return sorted(
        suggestions,
        key=lambda s: (s.quantity == 0, -(s.value or Decimal(0)), s.item.company_id, s.item.id),
    )
