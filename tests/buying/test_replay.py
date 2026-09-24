from decimal import Decimal

import pytest

from batchward.analysis.classification import ValueClass, VariabilityClass
from batchward.buying.cover import Cover, ItemClass
from batchward.buying.replay import (
    FLAT,
    WARM_UP_DAYS,
    Result,
    choose,
    flat_table,
    groups,
    replay,
    replay_grid,
    stretch,
    total,
    under,
    weekly_rates,
    with_shortfalls,
)

HISTORY = 182
AX = ItemClass(ValueClass.A, VariabilityClass.X)
CZ = ItemClass(ValueClass.C, VariabilityClass.Z)


def test_steady_demand_with_orders_on_time_is_all_served():
    demand = [10] * 120
    rates = [10.0] * 120
    result = replay(demand, rates, Cover(7, 7), start=0, end=120, took=[4] * 120, cost=2.0)
    measured = 120 - WARM_UP_DAYS
    assert (result.demanded, result.served, result.fill) == (10 * measured, 10 * measured, 1.0)
    # Stock is ordered when it falls below 10 x (4 + 7) = 110, which it first does at 100:
    # an order of 80 every 8 days, arriving with 60 still on the shelf.
    assert result.lines == measured // 8
    assert 60 * 2.0 <= result.stock_value <= 140 * 2.0
    assert result.items == 1


def test_orders_slower_than_the_rule_assumes_run_short_without_safety():
    demand = [10] * 120
    late = replay(demand, [10.0] * 120, Cover(0, 7), start=0, end=120, took=[7] * 120)
    safe = replay(demand, [10.0] * 120, Cover(4, 7), start=0, end=120, took=[7] * 120)
    assert late.fill < 1.0 and late.served < late.demanded
    assert safe.fill == 1.0


def test_a_replay_needs_days_to_measure_and_a_forecast_needs_history():
    with pytest.raises(ValueError, match="more days than its warm-up"):
        replay([1] * 20, [1.0] * 20, FLAT, start=0, end=WARM_UP_DAYS, took=[4] * 20)
    with pytest.raises(ValueError, match="182 days of demand"):
        weekly_rates([1] * 300, start=100, end=200)


def test_the_forecast_is_made_each_week_from_the_26_weeks_before():
    demand = [3] * HISTORY + [30] * 30
    rates = weekly_rates(demand, start=HISTORY, end=HISTORY + 30)
    assert rates[HISTORY : HISTORY + 7] == [3.0] * 7
    assert rates[HISTORY + 7] > 3.0  # the second week's forecast has seen a week at 30


def test_demand_is_what_was_sold_plus_what_went_short():
    demand = with_shortfalls({"I1": [2, 0, 5]}, {"I1": {1: 4}, "I2": {0: 1}},
                             items=["I1", "I2", "I3"], days=3)  # fmt: skip
    assert demand == {"I1": [2, 4, 5], "I2": [1, 0, 0], "I3": [0, 0, 0]}


def test_results_add_up_and_group_by_value_class_then_variability_class():
    ax, cz = Result(100, 99, 50.0, 3, 1), Result(10, 8, 5.0, 1, 1)
    assert total({AX: ax, CZ: cz}) == Result(110, 107, 55.0, 4, 2)
    assert Result().fill == 1.0
    grouped = groups({AX: ax, CZ: cz})
    assert list(grouped) == ["A", "C", "X", "Z"]
    assert grouped[ValueClass.C] == grouped[VariabilityClass.Z] == cz


def test_the_choice_is_the_least_stock_filling_as_well_with_no_more_lines():
    lean, leaner, worse = Cover(7, 7), Cover(4, 7), Cover(2, 7)
    results = {
        AX: {
            FLAT: Result(1000, 1000, 500.0, 10, 5),
            lean: Result(1000, 1000, 300.0, 12, 5),
            leaner: Result(1000, 999, 250.0, 12, 5),  # a tenth of a percent short
            worse: Result(1000, 900, 100.0, 12, 5),
        },
        CZ: {
            FLAT: Result(100, 95, 20.0, 6, 2),
            Cover(7, 28): Result(100, 95, 25.0, 2, 2),
            Cover(4, 28): Result(100, 95, 22.0, 2, 2),
            Cover(2, 28): Result(100, 90, 10.0, 2, 2),
        },
    }
    for rows in results.values():
        for cover in (FLAT, lean, leaner, worse, Cover(7, 28), Cover(4, 28), Cover(2, 28)):
            rows.setdefault(cover, Result(1, 0, 1e9, 99, 1))
    table = choose(results)
    # AX's leaner covers fill less; CZ's cheapest cover that fills as well is 4 + 28 days,
    # and ordering CZ monthly saves the lines AX's weekly orders add.
    assert (table.cover(AX), table.cover(CZ)) == (Cover(7, 7), Cover(4, 28))
    chosen = total(under(results, table))
    assert chosen.lines <= total(under(results, flat_table())).lines
    # Nothing was replayed for B or Y, so they keep the one-cover rule's days.
    assert table.cycle_days[ValueClass.B] == table.safety_days[VariabilityClass.Y] == 10.5


def test_with_nothing_better_the_one_cover_rule_is_chosen():
    results = {AX: {FLAT: Result(10, 10, 5.0, 1, 1), Cover(2, 7): Result(10, 5, 1.0, 1, 1)}}
    assert choose(results) == flat_table()


def test_a_grid_replays_each_item_that_sold_under_each_cover_by_its_class():
    days = HISTORY + 60
    demand = {"STEADY": [10] * days, "NEVER": [0] * days}
    costs = {"STEADY": Decimal("2.00")}
    period = stretch(demand, costs, start=HISTORY, end=days)
    assert {i: str(c) for i, c in period.classes.items()} == {"STEADY": "AX", "NEVER": "CZ"}
    replayed = replay_grid(demand, costs, period, took=[4] * days, covers=[FLAT, Cover(7, 7), FLAT])
    assert list(replayed) == [AX]
    assert sorted(replayed[AX]) == [Cover(7, 7), FLAT]
    assert replayed[AX][Cover(7, 7)].stock_value < replayed[AX][FLAT].stock_value
    assert all(result.fill == 1.0 and result.items == 1 for result in replayed[AX].values())
    # 7 + 7 days holds less but orders more often, and nothing else saves the lines.
    assert replayed[AX][Cover(7, 7)].lines > replayed[AX][FLAT].lines
    assert choose(replayed) == flat_table()
