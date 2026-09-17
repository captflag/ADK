"""ABC and XYZ classification: which items matter most, and how predictable they are.

ABC ranks items by consumption value — units sold at cost over a period.
Class A is the small set of items carrying roughly the first 80% of value,
B the next 15%, C the long tail. The item that crosses a boundary belongs to
the higher class, so a single dominant item is always A.

XYZ ranks items by the coefficient of variation of their weekly demand: X is
steady (below 0.5), Y variable (below 1.0), Z erratic or rare. An item that
never sold is Z, since nothing about its demand can be relied on.

Together they set how closely an item is watched: an AX item deserves tight
reorder control, a CZ item a question about whether to stock it at all.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from batchward.core.clock import ist_date
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, MovementType


class ValueClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class VariabilityClass(StrEnum):
    X = "X"
    Y = "Y"
    Z = "Z"


@dataclass(frozen=True, slots=True)
class Variability:
    cv: float | None
    """Coefficient of variation of weekly demand; None when nothing sold."""
    variability_class: VariabilityClass


def consumption_value(
    ledger: Ledger,
    costs: Mapping[BatchKey, Decimal],
    *,
    start: date,
    end: date,
) -> dict[str, Decimal]:
    """Units sold of each item between two local dates, inclusive, valued at batch cost.

    Sales of batches with no known cost cannot be valued and are left out.
    """
    values: defaultdict[str, Decimal] = defaultdict(Decimal)
    for m in ledger:
        if m.kind is not MovementType.SALE or ledger.is_reversed(m.id):
            continue
        cost = costs.get(m.batch)
        if cost is not None and start <= ist_date(m.at) <= end:
            values[m.batch.item_id] += cost * -m.qty
    return dict(values)


def classify_abc(
    values: Mapping[str, Decimal],
    *,
    a_share: float = 0.80,
    b_share: float = 0.95,
) -> dict[str, ValueClass]:
    if not 0 < a_share < b_share <= 1:
        raise ValueError("shares must satisfy 0 < a_share < b_share <= 1")
    total = sum(values.values(), Decimal(0))
    classes = {}
    cumulative = Decimal(0)
    for item_id, value in sorted(values.items(), key=lambda entry: (-entry[1], entry[0])):
        share_before = cumulative / total if total else Decimal(1)
        if value <= 0:
            classes[item_id] = ValueClass.C
        elif share_before < Decimal(str(a_share)):
            classes[item_id] = ValueClass.A
        elif share_before < Decimal(str(b_share)):
            classes[item_id] = ValueClass.B
        else:
            classes[item_id] = ValueClass.C
        cumulative += value
    return classes


def classify_xyz(
    weekly: Sequence[int],
    *,
    x_limit: float = 0.5,
    y_limit: float = 1.0,
) -> Variability:
    if not 0 < x_limit < y_limit:
        raise ValueError("limits must satisfy 0 < x_limit < y_limit")
    if not weekly or sum(weekly) == 0:
        return Variability(None, VariabilityClass.Z)
    cv = statistics.pstdev(weekly) / statistics.fmean(weekly)
    if cv < x_limit:
        return Variability(cv, VariabilityClass.X)
    if cv < y_limit:
        return Variability(cv, VariabilityClass.Y)
    return Variability(cv, VariabilityClass.Z)
