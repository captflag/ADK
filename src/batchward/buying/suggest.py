"""What to order: each item topped up to cover its forecast demand (ADR 0020).

An item's stock position is what will actually be there to sell: units on
sellable shelves, less what the forecast says will expire before it sells and
anything under a hold, plus what is still due on orders already placed. When
the position falls below the demand expected over the lead time and half the
cover, the item is ordered up to the demand over the lead time and the full
cover:

    reorder point = rate x (lead days + cover days / 2)
    order up to   = rate x (lead days + cover days)
    order         = order up to - position, when position < reorder point

The rate is the routed weekly forecast (ADR 0007) per day. An item that has not
sold is never ordered. Quantities are units; companies that sell only whole
cases need the order rounded up by a person, since Marg does not say the case
size.
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
from batchward.core.clock import end_of_day
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, Item, Location, MovementType

COVER_DAYS = 21
"""Days of demand an order tops stock up to, beyond the lead time."""
LEAD_DAYS = 4
"""Days from placing an order to the stock arriving."""


@dataclass(frozen=True, slots=True)
class Policy:
    cover_days: int = COVER_DAYS
    lead_days: int = LEAD_DAYS

    def __post_init__(self) -> None:
        if self.cover_days < 1 or self.lead_days < 0:
            raise ValueError("cover must be at least a day, and lead time cannot be negative")


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

    @property
    def position(self) -> int:
        return self.usable + self.due

    @property
    def value(self) -> Decimal | None:
        return None if self.rate is None else (self.rate * self.quantity).quantize(PAISA)

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
) -> list[Suggestion]:
    """A suggestion for every item that sells, those to order first, by value."""
    policy = policy or Policy()
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
        reorder_point = math.ceil(daily * (policy.lead_days + policy.cover_days / 2))
        order_up_to = math.ceil(daily * (policy.lead_days + policy.cover_days))
        on_hand = max(0, usable[item_id])
        coming = (due or {}).get(item_id, 0)
        position = on_hand + coming
        quantity = order_up_to - position if position < reorder_point else 0
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
            )
        )
    return sorted(
        suggestions,
        key=lambda s: (s.quantity == 0, -(s.value or Decimal(0)), s.item.company_id, s.item.id),
    )
