"""Choosing a forecasting method by demand pattern (ADR 0007).

The routes follow backtests, not the textbook. Croston's method and TSB are
designed for intermittent demand, yet on simulated weekly sales they only tied
simpler methods on intermittent items and over-forecast erratic and lumpy
items badly — excess stock, for a stockist. A moving average was best or tied
for the uneven patterns in every run, and exponential smoothing for smooth
demand. Reproduce with ``batchward backtest``; change a route only when a
backtest supports it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from batchward.analysis.demand import DemandPattern, aggregate, classify_demand, daily_sales
from batchward.analysis.forecast import Forecaster, exponential_smoothing, moving_average
from batchward.core.clock import end_of_day
from batchward.core.ledger import Ledger

ROUTES: dict[DemandPattern, tuple[str, Forecaster]] = {
    DemandPattern.SMOOTH: ("exponential smoothing", exponential_smoothing),
    DemandPattern.ERRATIC: ("moving average (8 weeks)", moving_average),
    DemandPattern.INTERMITTENT: ("moving average (8 weeks)", moving_average),
    DemandPattern.LUMPY: ("moving average (8 weeks)", moving_average),
}


@dataclass(frozen=True, slots=True)
class WeeklyForecast:
    pattern: DemandPattern
    method: str
    units_per_week: float


def forecast_weekly(weekly_history: Sequence[int]) -> WeeklyForecast:
    """Classify an item's weekly sales and forecast next week with the routed method."""
    pattern = classify_demand(weekly_history).pattern
    if pattern is DemandPattern.NO_DEMAND:
        return WeeklyForecast(pattern, "none: no sales in the history", 0.0)
    method, forecaster = ROUTES[pattern]
    return WeeklyForecast(pattern, method, forecaster(weekly_history))


def daily_rates(ledger: Ledger, *, on: date, weeks: int = 26) -> dict[str, float]:
    """Forecast units per day for every item that sold in the whole weeks before ``on``.

    The history ends the day before ``on``, as it stood then, so nothing from the
    day being planned leaks into its own forecast, not even a later correction.
    Items with no sales in the window are absent, which callers should read as
    "not selling".
    """
    if weeks < 1:
        raise ValueError("weeks must be at least 1")
    start = on - timedelta(days=weeks * 7)
    end = on - timedelta(days=1)
    history = daily_sales(ledger, start=start, end=end, as_of=end_of_day(end))
    return {
        item_id: forecast_weekly(aggregate(series, 7)).units_per_week / 7
        for item_id, series in history.items()
    }
