import pytest

from batchward.analysis.backtest import backtest, compare
from batchward.analysis.forecast import moving_average, tsb


def constant(value):
    return lambda history: value


def test_a_perfect_forecast_scores_zero_error():
    result = backtest([4, 4, 4, 4, 4, 4], constant(4), method="perfect", min_history=2)
    assert (result.mae, result.bias, result.wape) == (0, 0, 0)


def test_counts_one_origin_per_step_that_leaves_room_for_the_horizon():
    series = list(range(1, 11))
    assert backtest(series, constant(0), method="m", min_history=4, horizon=2).origins == 5
    assert backtest(series, constant(0), method="m", min_history=4, horizon=2, step=2).origins == 3


def test_only_history_before_each_origin_is_visible():
    seen = []

    def spy(history):
        seen.append(list(history))
        return 0

    backtest([1, 2, 3, 4], spy, method="spy", min_history=2)
    assert seen == [[1, 2], [1, 2, 3]]


def test_positive_bias_means_over_forecasting():
    result = backtest([2, 2, 2, 2], constant(5), method="high", min_history=1)
    assert result.bias == 3
    assert result.mae == 3


def test_wape_is_total_error_over_total_demand():
    # Forecast 3 against actuals 2 and 6: errors 1 and 3 over demand 8.
    result = backtest([9, 2, 6], constant(3), method="m", min_history=1)
    assert result.wape == pytest.approx(4 / 8)


def test_wape_is_undefined_without_demand():
    assert backtest([0, 0, 0], constant(1), method="m", min_history=1).wape is None


def test_mase_scales_error_by_the_naive_forecast_error():
    # Naive errors over the series: |5-1| + |1-5| + |5-1| = 12 over 3 steps = 4.
    result = backtest([1, 5, 1, 5], constant(3), method="m", min_history=1)
    assert result.mae == 2
    assert result.mase == pytest.approx(2 / 4)


def test_mase_is_undefined_when_the_naive_forecast_is_never_wrong():
    assert backtest([7, 7, 7], constant(7), method="m", min_history=1).mase is None


def test_rejects_a_series_too_short_for_history_and_horizon():
    with pytest.raises(ValueError, match="cannot hold 3 periods of history and a horizon of 2"):
        backtest([1, 2, 3, 4], constant(1), method="m", min_history=3, horizon=2)


def test_compare_ranks_methods_best_first():
    series = [4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    ranked = compare(
        series,
        {"moving average": moving_average, "tsb": tsb, "always ten": constant(10)},
        min_history=2,
    )
    assert [r.method for r in ranked][-1] == "always ten"
    assert ranked[0].mae <= ranked[1].mae <= ranked[2].mae
