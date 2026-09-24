"""``batchward cover-backtest``: choose each class's cover by replaying simulated demand.

The simulated stockist's demand is known in full: what was sold and what went
short. Replaying it under every cover in a grid, the first half of the days
after the forecast's 26 weeks of history chooses a table of covers by class
(ADR 0022), and the second half checks it against one cover of 21 days, and
against the table Batchward orders to.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from decimal import Decimal

from batchward.analysis.demand import daily_sales
from batchward.arguments import positive_int
from batchward.buying.cover import BY_CLASS, CoverTable
from batchward.buying.replay import (
    FLAT,
    HISTORY_WEEKS,
    WARM_UP_DAYS,
    Grid,
    Result,
    Stretch,
    choose,
    flat_table,
    grid_covers,
    groups,
    replay_grid,
    stretch,
    total,
    under,
    with_shortfalls,
)
from batchward.buying.suggest import last_rates
from batchward.reporting.inr import format_inr
from batchward.sim.business import SimConfig, simulate

_HISTORY = HISTORY_WEEKS * 7


def add_cover_commands(commands: argparse._SubParsersAction) -> None:
    cover = commands.add_parser(
        "cover-backtest",
        help="choose each class's cover by replaying simulated demand, and check the choice",
    )
    cover.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 2))
    cover.add_argument("--days", type=positive_int, default=728)
    cover.add_argument("--seed", type=int, default=42)
    cover.set_defaults(handler=_cover_backtest)


def _cover_backtest(args: argparse.Namespace) -> int:
    shortest = _HISTORY + 2 * (WARM_UP_DAYS + 7)
    if args.days < shortest:
        print(
            f"{args.days} days are too few: the forecast needs {_HISTORY} days of history, "
            f"and each half after it at least {WARM_UP_DAYS + 7}; give at least {shortest}.",
            file=sys.stderr,
        )
        return 1
    config = SimConfig(start=args.start, days=args.days, seed=args.seed)
    business = simulate(config)
    end = args.start + timedelta(days=args.days - 1)
    sales = daily_sales(business.ledger, start=args.start, end=end)
    shortfalls = {
        item_id: {(day - args.start).days: units for day, units in short.items()}
        for item_id, short in business.unmet_by_day.items()
    }
    items = [item.id for item in business.catalogue.items]
    demand = with_shortfalls(sales, shortfalls, items=items, days=args.days)
    costs = last_rates(business.ledger)
    rng = random.Random(args.seed)
    took = [rng.randint(*config.lead_time_days) for _ in range(args.days)]

    middle = _HISTORY + (args.days - _HISTORY) // 2
    chosen_on = stretch(demand, costs, start=_HISTORY, end=middle)
    checked_on = stretch(demand, costs, start=middle, end=args.days)
    covers = grid_covers()
    first = replay_grid(demand, costs, chosen_on, took=took, covers=covers)
    second = replay_grid(demand, costs, checked_on, took=took, covers=covers)
    table = choose(first)

    low, high = config.lead_time_days
    print(
        f"Cover backtest: {args.days} days from {args.start.isoformat()}, seed {args.seed}; "
        f"{len(items)} items, demand as chemists asked for it, orders arriving in "
        f"{low} to {high} days."
    )
    print(f"Chosen: {table}")
    if table == BY_CLASS:
        print("This is the table Batchward orders to (ADR 0022).")
    else:
        print(f"Batchward orders to: {BY_CLASS}")
    _compare(f"Chosen on {_span(args.start, chosen_on)}", first, table)
    _compare(f"Checked on {_span(args.start, checked_on)}", second, table)
    if table != BY_CLASS:
        _compare("Batchward's table, on the same days", second, BY_CLASS)
    return 0


def _compare(title: str, grid: Grid, table: CoverTable) -> None:
    flat, by_class = under(grid, flat_table()), under(grid, table)
    print(f"\n{title}")
    print(f"  {'':12}{f'one cover of {FLAT.days:g} days':>34}  {'cover by class':>34}")
    print(
        f"  {'Class':<6}{'Items':>6}{'Fill':>9}{'Stock at cost':>16}{'Lines':>9}"
        f"  {'Fill':>9}{'Stock at cost':>16}{'Lines':>9}"
    )
    for cell in sorted(grid):
        print(f"  {cell!s:<6}{flat[cell].items:>6}" + _row(flat[cell], by_class[cell]))
    print(f"  {'All':<6}{total(flat).items:>6}" + _row(total(flat), total(by_class)))
    print("  Judged by ABC class and by XYZ class:")
    flat_groups, class_groups = groups(flat), groups(by_class)
    for group, result in flat_groups.items():
        print(f"  {group!s:<6}{result.items:>6}" + _row(result, class_groups[group]))


def _row(flat: Result, by_class: Result) -> str:
    return f"{_cells(flat)}  {_cells(by_class)}"


def _cells(result: Result) -> str:
    stock = format_inr(Decimal(round(result.stock_value)))
    return f"{result.fill:>9.2%}{stock:>16}{result.lines:>9}"


def _span(start: date, period: Stretch) -> str:
    first = start + timedelta(days=period.start)
    last = start + timedelta(days=period.end - 1)
    return f"{first:%d/%m/%Y} to {last:%d/%m/%Y}"
