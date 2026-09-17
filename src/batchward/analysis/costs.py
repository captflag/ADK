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
from decimal import Decimal

from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, MovementType

PAISA = Decimal("0.01")


def batch_costs(ledger: Ledger) -> dict[BatchKey, Decimal]:
    """Quantity-weighted average purchase rate per batch, to the paisa."""
    units: defaultdict[BatchKey, int] = defaultdict(int)
    spend: defaultdict[BatchKey, Decimal] = defaultdict(Decimal)
    for m in ledger:
        if m.kind is not MovementType.PURCHASE or m.rate is None or ledger.is_reversed(m.id):
            continue
        units[m.batch] += m.qty
        spend[m.batch] += m.rate * m.qty
    return {key: (spend[key] / units[key]).quantize(PAISA) for key in units}


def value_at_cost(units: int, cost: Decimal | None) -> Decimal | None:
    """Units valued at a batch's cost, or None when the cost is unknown."""
    return None if cost is None else (cost * units).quantize(PAISA)
