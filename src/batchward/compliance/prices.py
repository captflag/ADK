"""Price Guard: ceiling prices as dated data, a check before billing, and exposure.

Under the Drugs (Prices Control) Order 2013, a scheduled formulation has a
ceiling price notified by NPPA, and its maximum retail price may not exceed
the ceiling plus GST. Ceilings are revised every 1 April, and new ones are
notified during the year, so a batch whose printed MRP was lawful when it was
made can be above the ceiling in force when it is sold. For a non-scheduled
formulation, the MRP may not rise more than 10% in twelve months.

Since the amendment of 30 June 2026, once a manufacturer shows it circulated a
revision, recovery of an overcharge is restricted to the stockist or
distributor who sold above the notified price, with 15% simple interest a year
from the date of sale. That is the exposure this module measures.

Batchward's reading, not legal advice:

- A sale of a scheduled formulation is **blocked** when the batch's printed MRP
  is above the ceiling plus GST in force on the sale date, and **warned** when
  no ceiling is on record, because a missing row is not proof of compliance.
- The overcharge on such a sale is the printed MRP minus the ceiling plus GST,
  for every unit sold. For a price rise it is the MRP beyond 110% of the lowest
  MRP of the same item made in the twelve months before. Returns are not
  netted, so the exposure errs high.
- Ceilings are per unit the distributor sells (a strip, a vial), excluding GST.
  NPPA notifies most per tablet; ``compliance.nppa`` converts a notification's
  table to prices per pack stocked (ADR 0013).
- The GST rate is the item's current rate; dated GST rates are not yet modelled.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import StrEnum

from batchward.core.clock import ist_date
from batchward.core.ledger import Ledger
from batchward.core.models import Batch, BatchKey, Item, MovementType, StockMovement

PAISA = Decimal("0.01")
INTEREST_RATE = Decimal("0.15")
"""Simple interest a year on an overcharge, from the date of sale."""
PRICE_RISE_CAP = Decimal("0.10")
"""The most a non-scheduled formulation's MRP may rise in twelve months."""
LOWEST_CEILING = PAISA
HIGHEST_CEILING = Decimal(10_000_000)
"""One crore rupees a unit: above any notified ceiling, and a guard against a slip of the keys."""


def _normal(text: str) -> str:
    """A formulation's words compared without case or spacing: "10MG" is "10 mg"."""
    return "".join(text.lower().split())


@dataclass(frozen=True, slots=True)
class CeilingPrice:
    molecule: str
    strength: str
    unit: str
    """The unit the distributor sells, as the item describes it, e.g. "strip of 10 tablets"."""
    ceiling: Decimal
    """Per unit sold, excluding GST."""
    effective_from: date
    reference: str
    """The notification it comes from, so every block can show its source."""

    def __post_init__(self) -> None:
        if not all(part.strip() for part in (self.molecule, self.strength, self.unit)):
            raise ValueError("a ceiling price must name the molecule, strength and unit")
        if not self.ceiling.is_finite() or not LOWEST_CEILING <= self.ceiling <= HIGHEST_CEILING:
            raise ValueError(
                f"a ceiling price must be between {LOWEST_CEILING} and {HIGHEST_CEILING} rupees "
                "a unit"
            )
        if not self.reference.strip():
            raise ValueError("a ceiling price must name the notification it comes from")

    @property
    def formulation(self) -> tuple[str, str, str]:
        return _normal(self.molecule), _normal(self.strength), _normal(self.unit)

    def max_retail_price(self, gst_rate: Decimal) -> Decimal:
        """The highest MRP this ceiling allows: ceiling plus GST, to the paisa."""
        return (self.ceiling * (1 + gst_rate)).quantize(PAISA, rounding=ROUND_HALF_UP)


class CeilingTable:
    """Every ceiling price ever notified, each in force from its date until replaced."""

    def __init__(self, prices: Iterable[CeilingPrice] = ()) -> None:
        self._by_formulation: defaultdict[tuple[str, str, str], list[CeilingPrice]] = defaultdict(
            list
        )
        for price in prices:
            self.add(price)

    def __len__(self) -> int:
        return sum(len(prices) for prices in self._by_formulation.values())

    def __iter__(self) -> Iterator[CeilingPrice]:
        """Every ceiling price, by formulation and then date."""
        for key in sorted(self._by_formulation):
            yield from self._by_formulation[key]

    def add(self, price: CeilingPrice) -> None:
        history = self._by_formulation[price.formulation]
        if any(p.effective_from == price.effective_from for p in history):
            raise ValueError(
                f"two ceiling prices for {price.molecule} {price.strength} {price.unit} take "
                f"effect on {price.effective_from.isoformat()}"
            )
        history.append(price)
        history.sort(key=lambda p: p.effective_from)

    def in_force(self, item: Item, on: date) -> CeilingPrice | None:
        """The ceiling for the item's formulation on a day, if one had been notified by then."""
        key = (_normal(item.molecule), _normal(item.strength), _normal(item.unit))
        applicable = [p for p in self._by_formulation.get(key, []) if p.effective_from <= on]
        return applicable[-1] if applicable else None


class Verdict(StrEnum):
    ALLOWED = "allowed"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class PriceCheck:
    batch: BatchKey
    on: date
    verdict: Verdict
    reason: str
    ceiling: CeilingPrice | None
    max_retail_price: Decimal | None
    excess_per_unit: Decimal
    """How far the printed MRP is above what is allowed; zero unless blocked."""


def check_batch_price(item: Item, batch: Batch, *, on: date, ceilings: CeilingTable) -> PriceCheck:
    """Whether a batch may be billed on a day, judged by its printed MRP (before billing)."""
    if batch.key.item_id != item.id:
        raise ValueError(f"batch {batch.key.batch_no} is not a batch of item {item.id}")
    if not item.dpco_scheduled:
        return PriceCheck(
            batch.key, on, Verdict.ALLOWED, "not a scheduled formulation", None, None, Decimal(0)
        )
    ceiling = ceilings.in_force(item, on)
    if ceiling is None:
        return PriceCheck(
            batch.key,
            on,
            Verdict.WARN,
            "scheduled formulation with no ceiling price on record; check the price before billing",
            None,
            None,
            Decimal(0),
        )
    limit = ceiling.max_retail_price(item.gst_rate)
    if batch.mrp > limit:
        return PriceCheck(
            batch.key,
            on,
            Verdict.BLOCK,
            f"printed MRP {batch.mrp} is above the ceiling of {limit} including GST, in force "
            f"since {ceiling.effective_from.isoformat()} under {ceiling.reference}",
            ceiling,
            limit,
            batch.mrp - limit,
        )
    return PriceCheck(
        batch.key,
        on,
        Verdict.ALLOWED,
        f"printed MRP {batch.mrp} is within the ceiling of {limit} including GST",
        ceiling,
        limit,
        Decimal(0),
    )


@dataclass(frozen=True, slots=True)
class PriceRise:
    batch: BatchKey
    mrp: Decimal
    reference_batch: BatchKey
    reference_mrp: Decimal
    """The lowest MRP of the same item among batches made in the twelve months before."""

    @property
    def limit(self) -> Decimal:
        """110% of the reference MRP, unrounded: an MRP above it is a rise of more than 10%."""
        return self.reference_mrp * (1 + PRICE_RISE_CAP)

    @property
    def allowed_mrp(self) -> Decimal:
        """The highest MRP in whole paise within the limit."""
        return self.limit.quantize(PAISA, rounding=ROUND_DOWN)

    @property
    def excess_per_unit(self) -> Decimal:
        return self.mrp - self.allowed_mrp

    @property
    def rise(self) -> Decimal:
        return self.mrp / self.reference_mrp - 1


def price_rises(batches: Iterable[Batch], items: Mapping[str, Item]) -> list[PriceRise]:
    """Batches of non-scheduled items whose MRP rose more than 10% within twelve months."""
    by_item: defaultdict[str, list[Batch]] = defaultdict(list)
    for batch in batches:
        item = items.get(batch.key.item_id)
        if item is not None and not item.dpco_scheduled:
            by_item[batch.key.item_id].append(batch)
    rises = []
    for item_batches in by_item.values():
        for batch in item_batches:
            window_start = batch.manufactured - timedelta(days=365)
            earlier = [
                b
                for b in item_batches
                if window_start <= b.manufactured < batch.manufactured and b.key != batch.key
            ]
            if not earlier:
                continue
            reference = min(earlier, key=lambda b: (b.mrp, b.manufactured, b.key))
            rise = PriceRise(batch.key, batch.mrp, reference.key, reference.mrp)
            if rise.mrp > rise.limit:
                rises.append(rise)
    return sorted(rises, key=lambda r: r.batch)


class Cause(StrEnum):
    ABOVE_CEILING = "printed MRP above the ceiling price in force"
    PRICE_RISE = "MRP rose more than 10% in twelve months"


@dataclass(frozen=True, slots=True)
class Overcharge:
    sale: StockMovement
    cause: Cause
    excess_per_unit: Decimal
    source: str
    """The notification, or the earlier batch, that sets the allowed price."""
    as_of: date

    @property
    def units(self) -> int:
        return -self.sale.qty

    @property
    def amount(self) -> Decimal:
        return self.excess_per_unit * self.units

    @property
    def days(self) -> int:
        return max(0, (self.as_of - ist_date(self.sale.at)).days)

    @property
    def interest(self) -> Decimal:
        return (self.amount * INTEREST_RATE * self.days / 365).quantize(
            PAISA, rounding=ROUND_HALF_UP
        )


@dataclass(frozen=True, slots=True)
class OverchargeExposure:
    as_of: date
    overcharges: tuple[Overcharge, ...]
    """Largest amount first."""

    @property
    def amount(self) -> Decimal:
        return sum((o.amount for o in self.overcharges), Decimal(0))

    @property
    def interest(self) -> Decimal:
        return sum((o.interest for o in self.overcharges), Decimal(0))

    @property
    def total(self) -> Decimal:
        return self.amount + self.interest

    def by_batch(self) -> dict[BatchKey, tuple[int, Decimal, Decimal]]:
        """Units, overcharge and interest for each batch, largest overcharge first."""
        totals: defaultdict[BatchKey, list] = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
        for o in self.overcharges:
            entry = totals[o.sale.batch]
            entry[0] += o.units
            entry[1] += o.amount
            entry[2] += o.interest
        return {
            key: (units, amount, interest)
            for key, (units, amount, interest) in sorted(
                totals.items(), key=lambda kv: (-kv[1][1], kv[0])
            )
        }


def overcharge_exposure(
    ledger: Ledger,
    batches: Mapping[BatchKey, Batch],
    items: Mapping[str, Item],
    ceilings: CeilingTable,
    *,
    as_of: date,
) -> OverchargeExposure:
    """Every sale up to ``as_of`` billed above the allowed price, with interest to that day."""
    rises = {rise.batch: rise for rise in price_rises(batches.values(), items)}
    overcharges = []
    for m in ledger:
        if m.kind is not MovementType.SALE or ledger.is_reversed(m.id):
            continue
        day = ist_date(m.at)
        if day > as_of:
            continue
        item, batch = items.get(m.batch.item_id), batches.get(m.batch)
        if item is None or batch is None:
            continue
        check = check_batch_price(item, batch, on=day, ceilings=ceilings)
        if check.verdict is Verdict.BLOCK and check.ceiling is not None:
            overcharges.append(
                Overcharge(
                    m, Cause.ABOVE_CEILING, check.excess_per_unit, check.ceiling.reference, as_of
                )
            )
        elif m.batch in rises:
            rise = rises[m.batch]
            overcharges.append(
                Overcharge(
                    m,
                    Cause.PRICE_RISE,
                    rise.excess_per_unit,
                    f"batch {rise.reference_batch.batch_no} at MRP {rise.reference_mrp}",
                    as_of,
                )
            )
    overcharges.sort(key=lambda o: (-o.amount, o.sale.at, o.sale.id))
    return OverchargeExposure(as_of=as_of, overcharges=tuple(overcharges))
