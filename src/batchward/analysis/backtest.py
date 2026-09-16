"""Scoring forecasting methods on history they have not seen.

A rolling-origin backtest stands at successive points in the past, forecasts
from only what was known then, and compares against what actually sold next.

Metrics:
- MAE: mean absolute error per period, in units.
- Bias: mean of forecast minus actual; positive means over-forecasting, which
  for a stockist means excess stock and expiry risk.
- WAPE: total absolute error over total actual demand; undefined with no demand.
- MASE: MAE divided by the MAE of a naive "same as last period" forecast over
  the whole series. Below 1 beats the naive forecast. Undefined when the naive
  forecast is never wrong.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from batchward.analysis.forecast import Forecaster


@dataclass(frozen=True, slots=True)
class BacktestResult:
    method: str
    origins: int
    mae: float
    bias: float
    wape: float | None
    mase: float | None


def backtest(
    series: Sequence[float],
    forecaster: Forecaster,
    *,
    method: str,
    min_history: int,
    horizon: int = 1,
    step: int = 1,
) -> BacktestResult:
    if min_history < 1 or horizon < 1 or step < 1:
        raise ValueError("min_history, horizon and step must be at least 1")
    if len(series) < min_history + horizon:
        raise ValueError(
            f"a series of {len(series)} periods cannot hold {min_history} periods of history "
            f"and a horizon of {horizon}"
        )

    absolute = signed = actual_total = 0.0
    errors = origins = 0
    for origin in range(min_history, len(series) - horizon + 1, step):
        forecast = forecaster(series[:origin])
        for actual in series[origin : origin + horizon]:
            absolute += abs(forecast - actual)
            signed += forecast - actual
            actual_total += actual
            errors += 1
        origins += 1

    mae = absolute / errors
    naive_scale = _naive_mae(series)
    return BacktestResult(
        method=method,
        origins=origins,
        mae=mae,
        bias=signed / errors,
        wape=absolute / actual_total if actual_total else None,
        mase=mae / naive_scale if naive_scale else None,
    )


def compare(
    series: Sequence[float],
    forecasters: Mapping[str, Forecaster],
    *,
    min_history: int,
    horizon: int = 1,
    step: int = 1,
) -> list[BacktestResult]:
    """Backtest every method on the same series, best first by MAE."""
    results = [
        backtest(
            series,
            forecaster,
            method=name,
            min_history=min_history,
            horizon=horizon,
            step=step,
        )
        for name, forecaster in forecasters.items()
    ]
    return sorted(results, key=lambda r: (r.mae, r.method))


def _naive_mae(series: Sequence[float]) -> float:
    if len(series) < 2:
        return 0.0
    return sum(abs(b - a) for a, b in pairwise(series)) / (len(series) - 1)
