"""Forecasting methods for demand that arrives in ones and twos with gaps.

Each method reads a history of demand per period and returns the expected
demand per future period. They are deliberately small and transparent: a
stockist, or a reviewer, can follow every step.

- Moving average and simple exponential smoothing suit smooth demand.
- Croston's method, with the Syntetos-Boylan correction for its known upward
  bias, forecasts intermittent demand by smoothing demand sizes and the
  intervals between them separately.
- TSB (Teunter-Syntetos-Babai) smooths the probability that demand occurs
  every period, so its forecast falls when demand stops arriving. Croston's
  cannot, because it only updates when demand happens; for a brand losing out
  to a rival, that difference decides whether stock is reordered or not.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

type Forecaster = Callable[[Sequence[float]], float]


def moving_average(history: Sequence[float], window: int = 8) -> float:
    if window <= 0:
        raise ValueError("window must be positive")
    recent = history[-window:]
    return sum(recent) / len(recent) if recent else 0.0


def exponential_smoothing(history: Sequence[float], alpha: float = 0.2) -> float:
    _check_rate(alpha, "alpha")
    if not history:
        return 0.0
    level = float(history[0])
    for demand in history[1:]:
        level += alpha * (demand - level)
    return level


def croston_sba(history: Sequence[float], alpha: float = 0.1) -> float:
    """Croston's method with the Syntetos-Boylan bias correction."""
    _check_rate(alpha, "alpha")
    size = interval = None
    since_last = 0
    for demand in history:
        since_last += 1
        if demand <= 0:
            continue
        if size is None:
            size, interval = float(demand), float(since_last)
        else:
            size += alpha * (demand - size)
            interval += alpha * (since_last - interval)
        since_last = 0
    if size is None or interval is None:
        return 0.0
    return (1 - alpha / 2) * size / interval


def tsb(history: Sequence[float], alpha: float = 0.1, beta: float = 0.1) -> float:
    """Teunter-Syntetos-Babai: smooth demand size and the chance demand occurs."""
    _check_rate(alpha, "alpha")
    _check_rate(beta, "beta")
    nonzero = [d for d in history if d > 0]
    if not nonzero:
        return 0.0
    probability = len(nonzero) / len(history)
    size = sum(nonzero) / len(nonzero)
    for demand in history:
        occurred = demand > 0
        probability += beta * (occurred - probability)
        if occurred:
            size += alpha * (demand - size)
    return probability * size


def _check_rate(value: float, name: str) -> None:
    if not 0 < value <= 1:
        raise ValueError(f"{name} must be in (0, 1]")
