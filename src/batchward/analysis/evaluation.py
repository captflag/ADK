"""Evaluating forecasting methods across a whole catalogue.

Each item's demand is summed into periods, classified by pattern, and every
method is backtested on it. Results are summarised per pattern, because the
right method depends on the pattern: what works for a steady seller can be
wrong for a slow, uneven one.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from batchward.analysis.backtest import backtest
from batchward.analysis.demand import DemandPattern, aggregate, classify_demand
from batchward.analysis.forecast import Forecaster


@dataclass(frozen=True, slots=True)
class MethodScore:
    method: str
    median_mase: float | None
    """Median across items; None when no item had a defined MASE."""
    mean_bias: float
    """Mean over items of forecast minus actual per period."""


@dataclass(frozen=True, slots=True)
class PatternReport:
    pattern: DemandPattern
    items: int
    scores: tuple[MethodScore, ...]
    """Best first by median MASE."""

    @property
    def best(self) -> MethodScore | None:
        return self.scores[0] if self.scores and self.scores[0].median_mase is not None else None


def evaluate(
    daily: Mapping[str, Sequence[int]],
    methods: Mapping[str, Forecaster],
    *,
    period: int = 7,
    min_history: int = 26,
) -> list[PatternReport]:
    """Backtest every method on every item with enough history, grouped by demand pattern.

    Items with fewer than ``min_history + 1`` whole periods, or no demand at all,
    are skipped: there is nothing to score them on.
    """
    mases: defaultdict[DemandPattern, defaultdict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    biases: defaultdict[DemandPattern, defaultdict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    items: defaultdict[DemandPattern, int] = defaultdict(int)

    for series in daily.values():
        periods = aggregate(series, period)
        if len(periods) <= min_history:
            continue
        pattern = classify_demand(periods).pattern
        if pattern is DemandPattern.NO_DEMAND:
            continue
        items[pattern] += 1
        for name, forecaster in methods.items():
            result = backtest(periods, forecaster, method=name, min_history=min_history)
            if result.mase is not None:
                mases[pattern][name].append(result.mase)
            biases[pattern][name].append(result.bias)

    reports = []
    for pattern in DemandPattern:
        if not items[pattern]:
            continue
        scores = [
            MethodScore(
                method=name,
                median_mase=statistics.median(mases[pattern][name])
                if mases[pattern][name]
                else None,
                mean_bias=statistics.fmean(biases[pattern][name]),
            )
            for name in methods
        ]
        scores.sort(key=lambda s: (s.median_mase is None, s.median_mase or 0.0, s.method))
        reports.append(PatternReport(pattern, items[pattern], tuple(scores)))
    return reports


def naive_last_period(history: Sequence[float]) -> float:
    """The benchmark every method must beat: next period repeats the last one."""
    return float(history[-1]) if history else 0.0
