"""Demand history derived from the ledger, and how to classify it.

Demand here means units chemists took away: sales, not netted against
returns. A near-expiry return is not negative demand; it is stock a chemist
failed to sell. A reversed sale never happened, so it is left out. Stockouts
hide demand the ledger never saw, so history from a business that ran short
understates what chemists wanted.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from batchward.core.clock import ist_date
from batchward.core.ledger import Ledger
from batchward.core.models import MovementType

ADI_CUTOFF = 1.32
"""Average demand interval above which demand counts as intermittent (Syntetos-Boylan)."""
CV2_CUTOFF = 0.49
"""Squared coefficient of variation of demand sizes above which demand counts as erratic."""


def daily_sales(
    ledger: Ledger, *, start: date, end: date, as_of: datetime | None = None
) -> dict[str, list[int]]:
    """Units sold of each item on each local day from ``start`` to ``end``, inclusive.

    Items with no sales in the window are absent from the result. A sale is left
    out if it has been reversed, or with ``as_of``, if it had been reversed by then,
    so a forecast made on a past day does not use corrections made after it.
    """
    days = (end - start).days + 1
    if days <= 0:
        raise ValueError("end must not be before start")
    series: defaultdict[str, list[int]] = defaultdict(lambda: [0] * days)
    for m in ledger:
        if m.kind is not MovementType.SALE or ledger.is_reversed(m.id, as_of):
            continue
        day = ist_date(m.at)
        if start <= day <= end:
            series[m.batch.item_id][(day - start).days] -= m.qty
    return dict(series)


def aggregate(series: Sequence[int], period: int) -> list[int]:
    """Sum a daily series into periods of ``period`` days.

    A trailing partial period is dropped, because a short period would look
    like a fall in demand.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    whole = len(series) // period
    return [sum(series[i * period : (i + 1) * period]) for i in range(whole)]


class DemandPattern(StrEnum):
    SMOOTH = "smooth"
    ERRATIC = "erratic"
    INTERMITTENT = "intermittent"
    LUMPY = "lumpy"
    NO_DEMAND = "no_demand"


@dataclass(frozen=True, slots=True)
class DemandProfile:
    pattern: DemandPattern
    periods: int
    periods_with_demand: int
    adi: float | None
    """Average interval between periods with demand; None when there was none."""
    cv2: float | None
    """Squared coefficient of variation of non-zero demand sizes; None when there was none."""


def classify_demand(series: Sequence[int]) -> DemandProfile:
    """Classify a demand series by how often and how evenly it occurs (Syntetos-Boylan).

    The classification is only as meaningful as the period the series is in;
    daily data for slow movers is almost always intermittent, so classify at the
    period you intend to forecast.
    """
    if any(x < 0 for x in series):
        raise ValueError("demand cannot be negative")
    nonzero = [x for x in series if x > 0]
    if not nonzero:
        return DemandProfile(DemandPattern.NO_DEMAND, len(series), 0, None, None)

    adi = len(series) / len(nonzero)
    mean = statistics.fmean(nonzero)
    cv2 = statistics.pvariance(nonzero) / mean**2

    frequent = adi < ADI_CUTOFF
    steady = cv2 < CV2_CUTOFF
    if frequent:
        pattern = DemandPattern.SMOOTH if steady else DemandPattern.ERRATIC
    else:
        pattern = DemandPattern.INTERMITTENT if steady else DemandPattern.LUMPY
    return DemandProfile(pattern, len(series), len(nonzero), adi, cv2)
