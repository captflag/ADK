import pytest
from hypothesis import given
from hypothesis import strategies as st

from batchward.analysis.forecast import (
    croston_sba,
    exponential_smoothing,
    moving_average,
    tsb,
)

histories = st.lists(st.integers(0, 60), max_size=80)


class TestMovingAverage:
    def test_averages_the_most_recent_window(self):
        assert moving_average([100, 2, 4, 6], window=3) == 4

    def test_uses_everything_when_history_is_shorter_than_the_window(self):
        assert moving_average([3, 5], window=8) == 4

    def test_empty_history_forecasts_nothing(self):
        assert moving_average([]) == 0


class TestExponentialSmoothing:
    def test_moves_a_fraction_of_the_way_towards_each_new_value(self):
        # level 10 -> 10 + 0.5 * (20 - 10) = 15 -> 15 + 0.5 * (15 - 15) = 15
        assert exponential_smoothing([10, 20, 15], alpha=0.5) == 15

    def test_empty_history_forecasts_nothing(self):
        assert exponential_smoothing([]) == 0


class TestCrostonSba:
    def test_forecasts_size_over_interval_with_the_bias_correction(self):
        # Demand of 6 every 3rd period: size 6, interval 3, corrected by (1 - 0.1 / 2).
        history = [0, 0, 6, 0, 0, 6, 0, 0, 6]
        assert croston_sba(history, alpha=0.1) == pytest.approx(0.95 * 6 / 3)

    def test_first_interval_counts_from_the_start_of_history(self):
        assert croston_sba([0, 0, 0, 8], alpha=0.1) == pytest.approx(0.95 * 8 / 4)

    def test_no_demand_forecasts_nothing(self):
        assert croston_sba([0, 0, 0]) == 0


class TestTsb:
    def test_regular_demand_forecasts_probability_times_size(self):
        history = [0, 5, 0, 5, 0, 5, 0, 5]
        assert tsb(history, alpha=0.1, beta=0.1) == pytest.approx(2.5, rel=0.05)

    def test_no_demand_forecasts_nothing(self):
        assert tsb([0, 0]) == 0

    def test_forecast_decays_when_demand_stops_but_crostons_does_not(self):
        selling = [4] * 20
        stopped = selling + [0] * 20
        assert tsb(stopped) < tsb(selling) / 3
        assert croston_sba(stopped) == pytest.approx(croston_sba(selling))


@pytest.mark.parametrize(
    "method", [moving_average, exponential_smoothing, croston_sba, tsb], ids=lambda f: f.__name__
)
@given(history=histories)
def test_forecasts_are_never_negative_and_never_exceed_the_largest_demand(method, history):
    forecast = method(history)
    assert forecast >= 0
    assert forecast <= max(history, default=0) + 1e-9


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        (exponential_smoothing, {"alpha": 0}),
        (croston_sba, {"alpha": 1.5}),
        (tsb, {"beta": -0.1}),
    ],
)
def test_smoothing_rates_must_be_between_zero_and_one(method, kwargs):
    with pytest.raises(ValueError, match="must be in"):
        method([1, 2, 3], **kwargs)
