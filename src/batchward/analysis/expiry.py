"""Stock at risk of expiring before it sells.

For each item, batches on sellable shelves are assumed to sell first-expiry-
first-out at the item's forecast daily rate. A batch can only sell on the days
before it expires, after the batches ahead of it have taken their share of
that demand. Whatever it cannot sell by then is at risk:

    expected_sold(batch) = min(units, max(0, rate x sellable_days - sold_by_earlier_batches))
    at_risk(batch)       = units - expected_sold(batch)

Stock on a shelf that is never sold from (a breakage and expiry shelf) is at
risk in full, and so is stock already past its expiry. The daily rate is a
forecast, so the result is an estimate: a guide to what to push, return or
claim early, not a certainty.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from batchward.analysis.costs import PAISA
from batchward.core.clock import end_of_day
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, Location


class RiskReason(StrEnum):
    WILL_NOT_SELL_IN_TIME = "will not sell before expiry at the forecast rate"
    UNSELLABLE_SHELF = "held where stock is never sold from"
    ALREADY_EXPIRED = "already past expiry"


@dataclass(frozen=True, slots=True)
class ExpiryRisk:
    batch: BatchKey
    location_id: str
    units: int
    days_to_expiry: int
    """Negative once the batch has expired."""
    expected_to_sell: int
    units_at_risk: int
    value_at_risk: Decimal | None
    reason: RiskReason


def expiry_exposure(
    ledger: Ledger,
    costs: Mapping[BatchKey, Decimal],
    locations: Iterable[Location],
    daily_rates: Mapping[str, float],
    *,
    on: date,
    within_days: int | None = None,
) -> list[ExpiryRisk]:
    """Every batch with units at risk, largest value first.

    Items without a forecast rate are treated as not selling at all, so all of
    their stock shows as at risk; supply a rate of zero deliberately or a real
    forecast. ``within_days`` limits the report to batches expiring that soon.
    """
    known = {location.id: location for location in locations}
    positions = ledger.balances(end_of_day(on))
    unknown = {location_id for _, location_id in positions} - known.keys()
    if unknown:
        raise ValueError(f"stock is held at locations not described: {sorted(unknown)}")

    risks: list[ExpiryRisk] = []
    sellable_by_item: defaultdict[str, list[tuple[BatchKey, str, int]]] = defaultdict(list)
    for (key, location_id), units in positions.items():
        days_left = (key.expiry - on).days
        if within_days is not None and days_left > within_days:
            continue
        if days_left <= 0:
            risks.append(
                _risk(key, location_id, units, days_left, 0, costs, RiskReason.ALREADY_EXPIRED)
            )
        elif not known[location_id].sellable:
            risks.append(
                _risk(key, location_id, units, days_left, 0, costs, RiskReason.UNSELLABLE_SHELF)
            )
        else:
            sellable_by_item[key.item_id].append((key, location_id, units))

    for item_id, batches in sellable_by_item.items():
        rate = max(0.0, daily_rates.get(item_id, 0.0))
        taken = 0.0
        for key, location_id, units in sorted(
            batches, key=lambda b: (b[0].expiry, b[0].batch_no, b[1])
        ):
            days_left = (key.expiry - on).days
            capacity = max(0.0, rate * days_left - taken)
            # The tolerance stops float noise (50.999...) costing a whole unit.
            sold = min(units, math.floor(capacity + 1e-9))
            taken += sold
            if sold < units:
                risks.append(
                    _risk(
                        key,
                        location_id,
                        units,
                        days_left,
                        sold,
                        costs,
                        RiskReason.WILL_NOT_SELL_IN_TIME,
                    )
                )

    return sorted(
        risks,
        key=lambda r: (-(r.value_at_risk or Decimal(0)), -r.units_at_risk, r.batch, r.location_id),
    )


def _risk(
    key: BatchKey,
    location_id: str,
    units: int,
    days_left: int,
    sold: int,
    costs: Mapping[BatchKey, Decimal],
    reason: RiskReason,
) -> ExpiryRisk:
    at_risk = units - sold
    cost = costs.get(key)
    return ExpiryRisk(
        batch=key,
        location_id=location_id,
        units=units,
        days_to_expiry=days_left,
        expected_to_sell=sold,
        units_at_risk=at_risk,
        value_at_risk=None if cost is None else (cost * at_risk).quantize(PAISA),
        reason=reason,
    )
