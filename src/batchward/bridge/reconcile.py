"""Compare the stock Marg reports with the stock its own bills add up to.

Marg keeps a running stock figure per batch alongside its bills. The two can
disagree — a bill deleted after the fact, a stock figure edited by hand — and
each disagreement is either a data-entry problem or unexplained shrinkage.
Either way a person should see it before the replayed ledger is trusted.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from batchward.bridge.marg_import import MargMasters
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey


@dataclass(frozen=True, slots=True)
class StockDifference:
    batch: BatchKey
    marg_stock: int
    """What Marg's batch record says is on hand."""
    ledger_stock: int
    """What Marg's bill lines add up to."""

    @property
    def difference(self) -> int:
        """Positive when Marg claims more stock than its bills explain."""
        return self.marg_stock - self.ledger_stock


def reconcile_stock(masters: MargMasters, ledger: Ledger) -> list[StockDifference]:
    """Every batch whose Marg stock disagrees with its bills, largest gap first."""
    from_bills: defaultdict[BatchKey, int] = defaultdict(int)
    for (key, _location), qty in ledger.balances().items():
        from_bills[key] += qty
    differences = [
        StockDifference(key, masters.stock.get(key, 0), from_bills.get(key, 0))
        for key in masters.stock.keys() | from_bills.keys()
        if masters.stock.get(key, 0) != from_bills.get(key, 0)
    ]
    return sorted(differences, key=lambda d: (-abs(d.difference), d.batch))
