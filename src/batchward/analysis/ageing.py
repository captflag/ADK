"""How long stock has been sitting, and which stock has stopped selling.

Ageing counts days since a batch first arrived in the business. Dead stock is
an item with sellable stock that has not sold for a set number of days. The
clock for an item that has never sold starts when it first arrived, so a new
launch is not written off as dead before it has had a chance.

Figures are at cost (see ``costs``). Units whose batch cost is unknown are
counted separately rather than valued at a guess.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal

from batchward.analysis.costs import PAISA
from batchward.core.clock import ist_date, ist_datetime
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, Location, MovementType

AGE_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0-30 days", 0, 30),
    ("31-60 days", 31, 60),
    ("61-90 days", 61, 90),
    ("91-180 days", 91, 180),
    ("over 180 days", 181, None),
)


@dataclass(frozen=True, slots=True)
class BatchAge:
    batch: BatchKey
    location_id: str
    units: int
    received: date
    age_days: int
    bucket: str
    value: Decimal | None


@dataclass(frozen=True, slots=True)
class DeadStock:
    item_id: str
    units: int
    value: Decimal
    """Value of the units whose batch cost is known."""
    unvalued_units: int
    last_sold: date | None
    idle_days: int
    """Days since the last sale, or since first arrival if the item never sold."""


def age_bucket(days: int) -> str:
    if days < 0:
        raise ValueError("age cannot be negative")
    for label, low, high in AGE_BUCKETS:
        if days >= low and (high is None or days <= high):
            return label
    raise AssertionError("unreachable: the last bucket is open-ended")


def stock_ageing(ledger: Ledger, costs: Mapping[BatchKey, Decimal], *, on: date) -> list[BatchAge]:
    """Every batch on hand at the end of a day, oldest first."""
    as_of = _end_of(on)
    received = _first_arrivals(ledger, as_of=on)
    ages = []
    for (key, location_id), units in ledger.balances(as_of).items():
        arrived = received[key]
        age = (on - arrived).days
        cost = costs.get(key)
        ages.append(
            BatchAge(
                batch=key,
                location_id=location_id,
                units=units,
                received=arrived,
                age_days=age,
                bucket=age_bucket(age),
                value=None if cost is None else (cost * units).quantize(PAISA),
            )
        )
    return sorted(ages, key=lambda a: (-a.age_days, a.batch, a.location_id))


def dead_stock(
    ledger: Ledger,
    costs: Mapping[BatchKey, Decimal],
    locations: Iterable[Location],
    *,
    on: date,
    idle_days: int = 120,
) -> list[DeadStock]:
    """Items with sellable stock and no sale for more than ``idle_days``, largest value first."""
    as_of = _end_of(on)
    positions = ledger.balances(as_of)
    sellable = _sellable_ids(positions, locations)

    units: defaultdict[str, int] = defaultdict(int)
    value: defaultdict[str, Decimal] = defaultdict(Decimal)
    unvalued: defaultdict[str, int] = defaultdict(int)
    for (key, location_id), qty in positions.items():
        if location_id not in sellable:
            continue
        units[key.item_id] += qty
        cost = costs.get(key)
        if cost is None:
            unvalued[key.item_id] += qty
        else:
            value[key.item_id] += cost * qty

    last_sold: dict[str, date] = {}
    first_arrived: dict[str, date] = {}
    for key, arrived in _first_arrivals(ledger, as_of=on).items():
        current = first_arrived.get(key.item_id)
        first_arrived[key.item_id] = arrived if current is None else min(current, arrived)
    for m in ledger:
        if m.kind is MovementType.SALE and m.at <= as_of and not ledger.is_reversed(m.id):
            day = ist_date(m.at)
            if day > last_sold.get(m.batch.item_id, date.min):
                last_sold[m.batch.item_id] = day

    dead = []
    for item_id, qty in units.items():
        if qty <= 0:
            continue
        since = max(last_sold.get(item_id, date.min), first_arrived[item_id])
        idle = (on - since).days
        if idle > idle_days:
            dead.append(
                DeadStock(
                    item_id=item_id,
                    units=qty,
                    value=value[item_id].quantize(PAISA),
                    unvalued_units=unvalued[item_id],
                    last_sold=last_sold.get(item_id),
                    idle_days=idle,
                )
            )
    return sorted(dead, key=lambda d: (-d.value, -d.units, d.item_id))


def _first_arrivals(ledger: Ledger, *, as_of: date) -> dict[BatchKey, date]:
    """The local day each batch first came into the business, up to and including ``as_of``.

    A purchase is the arrival; a batch with no purchase on record (an opening
    adjustment, say) arrived with its first stock-adding movement.
    """
    purchased: dict[BatchKey, date] = {}
    added: dict[BatchKey, date] = {}
    for m in ledger:
        day = ist_date(m.at)
        if day > as_of or m.qty <= 0 or ledger.is_reversed(m.id):
            continue
        if m.kind is MovementType.PURCHASE and day < purchased.get(m.batch, date.max):
            purchased[m.batch] = day
        if day < added.get(m.batch, date.max):
            added[m.batch] = day
    return added | purchased


def _sellable_ids(
    positions: Mapping[tuple[BatchKey, str], int], locations: Iterable[Location]
) -> set[str]:
    """Ids of sellable locations, refusing stock held anywhere not described."""
    known = {location.id: location for location in locations}
    unknown = {location_id for _, location_id in positions} - known.keys()
    if unknown:
        raise ValueError(f"stock is held at locations not described: {sorted(unknown)}")
    return {location_id for location_id, location in known.items() if location.sellable}


def _end_of(day: date):
    return ist_datetime(day, time(23, 59, 59))
