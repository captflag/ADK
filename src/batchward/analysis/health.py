"""A stockist's stock health on one day, in rupees at cost.

This gathers the separate analyses into the handful of figures an owner acts
on: how much money is sitting in stock and for how long, how much of it has
stopped selling, how much is likely to expire before it sells, and which items
carry the business. Every figure is computed here, in code (ADR 0003).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from batchward.analysis.ageing import AGE_BUCKETS, DeadStock, dead_stock, stock_ageing
from batchward.analysis.classification import ValueClass, classify_abc, consumption_value
from batchward.analysis.costs import batch_costs
from batchward.analysis.expiry import ExpiryRisk, expiry_exposure
from batchward.analysis.routing import daily_rates
from batchward.core.clock import end_of_day
from batchward.core.ledger import Ledger
from batchward.core.models import Location


@dataclass(frozen=True, slots=True)
class StockHealth:
    on: date
    stock_value: Decimal
    """All stock on hand at cost, wherever it is held."""
    unvalued_units: int
    """Units on hand whose batch cost is unknown, and so missing from every value."""
    value_by_age: dict[str, Decimal]
    dead_stock: tuple[DeadStock, ...]
    expiry_risks: tuple[ExpiryRisk, ...]
    consumption_by_class: dict[ValueClass, Decimal]
    items_by_class: dict[ValueClass, int]

    @property
    def dead_stock_value(self) -> Decimal:
        return sum((d.value for d in self.dead_stock), Decimal(0))

    @property
    def expiry_value_at_risk(self) -> Decimal:
        return sum((r.value_at_risk or Decimal(0) for r in self.expiry_risks), Decimal(0))


def stock_health(
    ledger: Ledger,
    locations: Iterable[Location],
    *,
    on: date,
    dead_after_days: int = 120,
    expiry_within_days: int = 180,
    forecast_weeks: int = 26,
    consumption_days: int = 365,
) -> StockHealth:
    locations = list(locations)
    costs = batch_costs(ledger, as_of=end_of_day(on))

    ages = stock_ageing(ledger, costs, on=on)
    value_by_age = {label: Decimal(0) for label, _, _ in AGE_BUCKETS}
    unvalued = 0
    for age in ages:
        if age.value is None:
            unvalued += age.units
        else:
            value_by_age[age.bucket] += age.value

    dead = dead_stock(ledger, costs, locations, on=on, idle_days=dead_after_days)
    rates = daily_rates(ledger, on=on, weeks=forecast_weeks)
    risks = expiry_exposure(ledger, costs, locations, rates, on=on, within_days=expiry_within_days)

    consumption = consumption_value(
        ledger,
        costs,
        start=on - timedelta(days=consumption_days),
        end=on - timedelta(days=1),
        as_of=end_of_day(on),
    )
    classes = classify_abc(consumption)
    consumption_by_class = {value_class: Decimal(0) for value_class in ValueClass}
    items_by_class = dict.fromkeys(ValueClass, 0)
    for item_id, value_class in classes.items():
        consumption_by_class[value_class] += consumption[item_id]
        items_by_class[value_class] += 1

    return StockHealth(
        on=on,
        stock_value=sum(value_by_age.values(), Decimal(0)),
        unvalued_units=unvalued,
        value_by_age=value_by_age,
        dead_stock=tuple(dead),
        expiry_risks=tuple(risks),
        consumption_by_class=consumption_by_class,
        items_by_class=items_by_class,
    )
