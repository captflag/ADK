"""Replaying order rules over past demand, to choose each class's cover (ADR 0022).

A replay runs one item's stock day by day under a cover: each morning what
was ordered arrives, the item is ordered if the stock that will be there to
sell has fallen below the lead time and the safety, and then the day's demand
is served from what is on the shelf; what cannot be served is lost. The
forecast is the one Batchward uses (ADR 0007), made each week from the 26
weeks of demand before it. Orders take as long as they took in the history
being replayed, which is not always the lead time the rule assumes.

Every cover in a grid is replayed on every item, and the results are added up
by ABC-XYZ class. From them a table of safety by XYZ class and days per order
by ABC class is chosen: the least stock at cost that fills as many of the
units asked for as one cover of 21 days did (to within a twentieth of a
percent), judged for each ABC class and each XYZ class, with no more order
lines in all. Choosing on one stretch of demand and checking on the next shows
whether the choice holds.

The replay leaves out what it cannot see: expiry (holding more of a slow mover
risks more of it expiring), case sizes, and the days a company takes orders.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from batchward.analysis.classification import ValueClass, VariabilityClass
from batchward.analysis.demand import aggregate
from batchward.analysis.routing import forecast_weekly
from batchward.buying.cover import Cover, CoverTable, ItemClass, classify
from batchward.buying.suggest import COVER_DAYS, LEAD_DAYS

HISTORY_WEEKS = 26
WARM_UP_DAYS = 14
"""Days at the start of a replay left out of its results, while stock settles to the rule."""
TOLERANCE = 0.0005
"""How far below the one-cover rule's fill rate a class may fall: a twentieth of a percent."""
SAFETY_DAYS = (2, 4, 7, 10.5, 14, 21)
CYCLE_DAYS = (7, 10.5, 14, 21, 28)
FLAT = Cover.flat(COVER_DAYS)


@dataclass(frozen=True, slots=True)
class Result:
    """What a rule did over the days measured, for one item or added up for many."""

    demanded: int = 0
    served: int = 0
    stock_value: float = 0.0
    """Average stock held at the end of each day, at cost."""
    lines: int = 0
    """Orders placed: one line on a purchase order each."""
    items: int = 0

    @property
    def fill(self) -> float:
        """Share of the units asked for that were served; 1 when none were asked for."""
        return self.served / self.demanded if self.demanded else 1.0

    def __add__(self, other: Result) -> Result:
        return Result(
            self.demanded + other.demanded,
            self.served + other.served,
            self.stock_value + other.stock_value,
            self.lines + other.lines,
            self.items + other.items,
        )


def weekly_rates(demand: Sequence[int], *, start: int, end: int) -> list[float]:
    """The forecast per day for each day from ``start`` to ``end``, made each week.

    Each week's forecast uses the 26 weeks of demand before it, as the live
    forecast uses the 26 weeks of sales before the day (ADR 0007).
    """
    history = HISTORY_WEEKS * 7
    if start < history:
        raise ValueError(f"a replay needs {history} days of demand before it starts")
    rates = [0.0] * end
    for week in range(start, end, 7):
        weekly = aggregate(demand[week - history : week], 7)
        rate = forecast_weekly(weekly).units_per_week / 7
        rates[week : min(week + 7, end)] = [rate] * (min(week + 7, end) - week)
    return rates


def replay(
    demand: Sequence[int],
    rates: Sequence[float],
    cover: Cover,
    *,
    start: int,
    end: int,
    took: Sequence[int],
    lead_days: int = LEAD_DAYS,
    cost: float = 0.0,
) -> Result:
    """One item under one cover from day ``start`` to ``end``.

    ``took[d]`` is how many days an order placed on day ``d`` took to arrive.
    Stock starts halfway through an order's cover, as it would part way through
    the rule's cycle, and the first days are left out of the result.
    """
    if not start + WARM_UP_DAYS < end <= len(demand):
        raise ValueError("a replay needs more days than its warm-up, within the demand given")
    on_hand = math.ceil(rates[start] * (lead_days + cover.safety_days + cover.cycle_days / 2))
    arriving: defaultdict[int, int] = defaultdict(int)
    on_order = demanded = served = lines = held = 0
    measured = start + WARM_UP_DAYS
    for day in range(start, end):
        arrived = arriving.pop(day, 0)
        on_hand += arrived
        on_order -= arrived
        rate = rates[day]
        if rate > 0 and on_hand + on_order < rate * (lead_days + cover.safety_days):
            quantity = math.ceil(rate * (lead_days + cover.days)) - on_hand - on_order
            if quantity > 0:
                arriving[day + took[day]] += quantity
                on_order += quantity
                lines += day >= measured
        sold = min(on_hand, demand[day])
        on_hand -= sold
        if day >= measured:
            demanded += demand[day]
            served += sold
            held += on_hand
    return Result(demanded, served, held / (end - measured) * cost, lines, items=1)


Grid = dict[ItemClass, dict[Cover, Result]]


@dataclass(frozen=True, slots=True)
class Stretch:
    """Days of demand to replay, with the classes items had when it began."""

    start: int
    end: int
    classes: dict[str, ItemClass]


def stretch(
    demand: Mapping[str, Sequence[int]],
    costs: Mapping[str, Decimal],
    *,
    start: int,
    end: int,
) -> Stretch:
    """A stretch of days, classing each item by the year and 26 weeks of demand before it."""
    values = {
        item_id: sum(series[max(0, start - 365) : start]) * costs.get(item_id, Decimal(0))
        for item_id, series in demand.items()
    }
    weekly = {
        item_id: aggregate(series[start - HISTORY_WEEKS * 7 : start], 7)
        for item_id, series in demand.items()
    }
    return Stretch(start, end, classify(values, weekly, demand))


def replay_grid(
    demand: Mapping[str, Sequence[int]],
    costs: Mapping[str, Decimal],
    period: Stretch,
    *,
    took: Sequence[int],
    covers: Iterable[Cover],
    lead_days: int = LEAD_DAYS,
) -> Grid:
    """Every cover replayed on every item that sold before the stretch, added up by class."""
    covers = sorted(set(covers))
    grid: Grid = {}
    for item_id, series in demand.items():
        if not any(series[period.start - HISTORY_WEEKS * 7 : period.start]):
            continue
        rates = weekly_rates(series, start=period.start, end=period.end)
        cost = float(costs.get(item_id, 0))
        row = grid.setdefault(period.classes[item_id], {})
        for cover in covers:
            result = replay(
                series,
                rates,
                cover,
                start=period.start,
                end=period.end,
                took=took,
                lead_days=lead_days,
                cost=cost,
            )
            row[cover] = row.get(cover, Result()) + result
    return grid


def grid_covers() -> list[Cover]:
    """Every safety and cycle the choice looks at, with the one-cover rule among them."""
    covers = {Cover(s, c) for s, c in itertools.product(SAFETY_DAYS, CYCLE_DAYS)}
    return sorted(covers | {FLAT})


def under(grid: Grid, table: CoverTable) -> dict[ItemClass, Result]:
    """What each class did under a table."""
    return {cell: rows[table.cover(cell)] for cell, rows in grid.items()}


def flat_table(cover: Cover = FLAT) -> CoverTable:
    return CoverTable(
        safety_days=dict.fromkeys(VariabilityClass, cover.safety_days),
        cycle_days=dict.fromkeys(ValueClass, cover.cycle_days),
    )


def groups(results: Mapping[ItemClass, Result]) -> dict[ValueClass | VariabilityClass, Result]:
    """Results added up by ABC class and by XYZ class: A, B and C, then X, Y and Z."""
    added: dict[ValueClass | VariabilityClass, Result] = {}
    for group in (*ValueClass, *VariabilityClass):
        within = [r for cell, r in results.items() if group in (cell.value, cell.variability)]
        if within:
            added[group] = sum(within, Result())
    return added


def choose(grid: Grid, *, tolerance: float = TOLERANCE) -> CoverTable:
    """The table holding the least stock that fills as well as one cover did.

    The fill is judged for each ABC class and each XYZ class, all its items
    together: an ABC-XYZ class such as AZ can hold a single item, whose luck
    would decide the choice. "As well" is to within ``tolerance``, and the table
    may place no more order lines in all. The one-cover rule meets both, so a
    table is always found. A class with no items replayed keeps the one-cover
    rule's days, since nothing speaks for changing them.
    """
    baseline = under(grid, flat_table())
    most_lines = sum(result.lines for result in baseline.values())
    filled = groups(baseline)
    safeties = sorted({cover.safety_days for rows in grid.values() for cover in rows})
    cycles = sorted({cover.cycle_days for rows in grid.values() for cover in rows})
    varying = sorted({cell.variability for cell in grid})
    valued = sorted({cell.value for cell in grid})
    best: tuple[float, CoverTable] | None = None
    for safety in itertools.product(safeties, repeat=len(varying)):
        for cycle in itertools.product(cycles, repeat=len(valued)):
            table = CoverTable(
                safety_days={
                    **dict.fromkeys(VariabilityClass, FLAT.safety_days),
                    **dict(zip(varying, safety, strict=True)),
                },
                cycle_days={
                    **dict.fromkeys(ValueClass, FLAT.cycle_days),
                    **dict(zip(valued, cycle, strict=True)),
                },
            )
            results = _under_if_replayed(grid, table)
            if results is None:
                continue
            if sum(result.lines for result in results.values()) > most_lines:
                continue
            judged = groups(results)
            if any(judged[group].fill < filled[group].fill - tolerance for group in judged):
                continue
            stock = sum(result.stock_value for result in results.values())
            if best is None or stock < best[0]:
                best = (stock, table)
    assert best is not None, "the one-cover rule always qualifies"
    return best[1]


def _under_if_replayed(grid: Grid, table: CoverTable) -> dict[ItemClass, Result] | None:
    results = {}
    for cell, rows in grid.items():
        result = rows.get(table.cover(cell))
        if result is None:
            return None
        results[cell] = result
    return results


def total(results: Mapping[ItemClass, Result]) -> Result:
    return sum(results.values(), Result())


def with_shortfalls(
    sales: Mapping[str, Sequence[int]],
    shortfalls: Mapping[str, Mapping[int, int]],
    *,
    items: Iterable[str],
    days: int,
) -> dict[str, list[int]]:
    """Demand as chemists asked for it: what was sold, plus what went short, day by day."""
    demand = {}
    for item_id in items:
        series = list(sales.get(item_id, [0] * days))
        for day, units in shortfalls.get(item_id, {}).items():
            series[day] += units
        demand[item_id] = series
    return demand
