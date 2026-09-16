from batchward.analysis.demand import DemandPattern
from batchward.analysis.evaluation import evaluate, naive_last_period
from batchward.analysis.forecast import moving_average


def steady(weeks, per_week=14):
    return [per_week // 7] * (weeks * 7)


def rare(weeks):
    # One week in four sells 12 units, all on the Monday.
    return [12 if day % 28 == 0 else 0 for day in range(weeks * 7)]


def always(value):
    return lambda history: value


def test_groups_items_by_their_weekly_demand_pattern():
    reports = evaluate(
        {"A": steady(40), "B": steady(40), "C": rare(40)},
        {"naive": naive_last_period},
        min_history=26,
    )
    assert {r.pattern: r.items for r in reports} == {
        DemandPattern.SMOOTH: 2,
        DemandPattern.INTERMITTENT: 1,
    }


def test_ranks_methods_best_first_within_a_pattern():
    (report,) = evaluate(
        {"A": [1, 2] * 140},
        {"far off": always(50), "moving average": moving_average, "naive": naive_last_period},
        min_history=26,
    )
    assert report.scores[0].method == "moving average"
    assert report.best.method == "moving average"


def test_reports_mean_bias_so_over_forecasting_is_visible():
    (report,) = evaluate({"A": steady(40)}, {"too high": always(20)}, min_history=26)
    (score,) = report.scores
    assert score.mean_bias == 6  # forecast 20 a week against 14 sold


def test_skips_items_without_enough_history_or_without_demand():
    reports = evaluate(
        {"short": steady(20), "silent": [0] * 280, "ok": steady(40)},
        {"naive": naive_last_period},
        min_history=26,
    )
    assert sum(r.items for r in reports) == 1


def test_a_method_with_no_defined_mase_is_ranked_last_and_is_never_best():
    # Constant demand: naive is never wrong, so MASE is undefined for every method.
    (report,) = evaluate({"flat": steady(40)}, {"naive": naive_last_period}, min_history=26)
    assert report.scores[0].median_mase is None
    assert report.best is None


def test_naive_repeats_the_last_period():
    assert naive_last_period([3, 9]) == 9
    assert naive_last_period([]) == 0
