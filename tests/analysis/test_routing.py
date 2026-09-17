from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from batchward.analysis.demand import DemandPattern
from batchward.analysis.forecast import exponential_smoothing, moving_average
from batchward.analysis.routing import ROUTES, daily_rates, forecast_weekly
from batchward.core.ledger import Ledger
from batchward.core.models import MovementType
from factories import at, batch_key, movement


def test_every_pattern_with_demand_has_a_route():
    assert set(ROUTES) == set(DemandPattern) - {DemandPattern.NO_DEMAND}


@pytest.mark.parametrize(
    ("history", "pattern", "forecaster"),
    [
        ([10, 11, 9, 10, 12, 10, 9, 11], DemandPattern.SMOOTH, exponential_smoothing),
        ([1, 40, 2, 35, 1, 50, 3, 45], DemandPattern.ERRATIC, moving_average),
        ([0, 0, 5, 0, 0, 6, 0, 0, 5, 0], DemandPattern.INTERMITTENT, moving_average),
        ([0, 0, 1, 0, 0, 40, 0, 0, 2, 0], DemandPattern.LUMPY, moving_average),
    ],
)
def test_forecasts_with_the_method_routed_for_the_items_pattern(history, pattern, forecaster):
    result = forecast_weekly(history)
    assert result.pattern is pattern
    assert result.units_per_week == forecaster(history)
    assert result.method == ROUTES[pattern][0]


def test_an_item_that_never_sold_is_forecast_at_zero_and_says_why():
    result = forecast_weekly([0, 0, 0, 0])
    assert result.pattern is DemandPattern.NO_DEMAND
    assert result.units_per_week == 0
    assert "no sales" in result.method


@given(st.lists(st.integers(0, 80), min_size=1, max_size=60))
def test_a_routed_forecast_stays_within_what_was_ever_sold(history):
    assert 0 <= forecast_weekly(history).units_per_week <= max(history) + 1e-9


def test_daily_rates_forecast_each_item_from_the_weeks_before_the_day():
    unsold = batch_key("X1", item_id="I002")
    sales = [movement(MovementType.SALE, -2, when=at(d, month=2)) for d in range(1, 29)]
    ledger = Ledger(
        [
            movement(MovementType.PURCHASE, 500, when=at(1)),
            movement(MovementType.PURCHASE, 500, when=at(1), batch=unsold),
            *sales,
            # A sale on the day being planned must not count.
            movement(MovementType.SALE, -300, when=at(1, month=3)),
        ]
    )
    rates = daily_rates(ledger, on=date(2026, 3, 1), weeks=4)
    assert rates == {"I001": pytest.approx(2.0)}
