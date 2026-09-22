"""What stock cost to buy.

Every rupee figure in stock health — stock value, dead stock, value at risk of
expiry — is at cost, because that is the money a stockist has tied up. The
cost of a batch is the average rate it was bought at, weighted by quantity.
Reversed purchases never happened and are left out; a batch with no purchase
rate on record has no cost, and figures that need one say so rather than
guessing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, MovementType

PAISA = Decimal("0.01")


def batch_costs(ledger: Ledger, *, as_of: datetime | None = None) -> dict[BatchKey, Decimal]:
    """Quantity-weighted average purchase rate per batch, to the paisa.

    With ``as_of``, only purchases made by then count, and a purchase reversed
    later still does, so a past day is valued as it stood.
    """
    units: defaultdict[BatchKey, int] = defaultdict(int)
    spend: defaultdict[BatchKey, Decimal] = defaultdict(Decimal)
    for m in ledger:
        if m.kind is not MovementType.PURCHASE or m.rate is None:
            continue
        if (as_of is not None and m.at > as_of) or ledger.is_reversed(m.id, as_of):
            continue
        units[m.batch] += m.qty
        spend[m.batch] += m.rate * m.qty
    return {key: (spend[key] / units[key]).quantize(PAISA) for key in units}


def value_at_cost(units: int, cost: Decimal | None) -> Decimal | None:
    """Units valued at a batch's cost, or None when the cost is unknown."""
    return None if cost is None else (cost * units).quantize(PAISA)
