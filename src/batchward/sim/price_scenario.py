"""Ceiling prices for the simulated stockist, and the price problems the Price Guard must find.

The simulator prices every brand of a scheduled formulation within one ceiling,
so on its own nothing is ever overcharged. This scenario adds the history that
makes the Price Guard's job real:

- a ceiling for every scheduled formulation from 1 April 2023, set so every
  brand is compliant;
- the annual revision on 1 April 2026, raising ceilings by 0.64956%, the
  wholesale price index change NPPA notified that year;
- a lower ceiling for one formulation from the same day, which the three
  most expensive brands' printed MRPs now exceed. The stockist keeps selling
  their existing stock, as the trade often does. The formulation is the named
  molecule's, unless its brands are priced too closely for any ceiling to put
  exactly three above it; then it is the first formulation whose brands allow it;
- one non-scheduled brand whose new batch carries an MRP 18% above the batch
  made months earlier, and is sold anyway.

Ceilings are dated records, so they are built for any simulation. The price
rise is stock movements, so it is added only when the simulation runs from the
batch's arrival through the days it sells on; nothing is recorded outside the
simulated period. Notification references are fictional and marked SIM.
Ceilings here are per unit sold, not per tablet.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, time, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from batchward.compliance.prices import PAISA, CeilingPrice, CeilingTable
from batchward.core.clock import ist_date, ist_datetime
from batchward.core.models import Batch, BatchKey, Item, MovementType, StockMovement
from batchward.sim.business import Business, location_for, price_to_retailer, price_to_stockist

FIRST_NOTIFIED = date(2023, 4, 1)
REVISION_DAY = date(2026, 4, 1)
RISE_RECEIVED = date(2026, 2, 2)
RISE_SELLING_DAYS = 50
"""The price-rise batch sells on days up to this many days after it arrives."""
WPI_CHANGE_2026 = Decimal("0.0064956")
BRANDS_OVER_CEILING = 3


@dataclass(frozen=True, slots=True)
class PriceScenario:
    ceilings: CeilingTable
    lowered: CeilingPrice
    """The ceiling lowered on the revision day."""
    over_ceiling: frozenset[str]
    """Items whose printed MRP is above the lowered ceiling."""
    price_rise: BatchKey | None
    """The batch whose MRP rose more than 10%, or None when the simulation cannot hold it."""
    price_rise_units_sold: int


def seed_price_control(
    business: Business,
    *,
    lowered_molecule: str = "Atorvastatin",
    rise: Decimal = Decimal("0.18"),
    rise_received: date = RISE_RECEIVED,
    seed: int = 2026,
) -> PriceScenario:
    """Build the ceiling history, and add the price-rise batch and its sales to ``business``.

    The simulated period is read from the ledger, first movement to last. The
    rise is added only if that period runs from ``rise_received`` for
    ``RISE_SELLING_DAYS`` more days.
    """
    by_formulation: defaultdict[tuple[str, str, str], list[Item]] = defaultdict(list)
    for item in business.catalogue.items:
        if item.dpco_scheduled:
            by_formulation[(item.molecule, item.strength, item.unit)].append(item)

    formulations = sorted(by_formulation.items())
    if not any(molecule == lowered_molecule for (molecule, _, _), _ in formulations):
        raise LookupError(f"the catalogue has no scheduled {lowered_molecule} brand")
    named_first = sorted(formulations, key=lambda entry: entry[0][0] != lowered_molecule)
    choice = next(
        (
            (formulation, split)
            for formulation, brands in named_first
            if (split := _lower_below_three(formulation, brands)) is not None
        ),
        None,
    )
    if choice is None:
        raise ValueError(
            "no scheduled formulation's brands are priced far enough apart for a ceiling to put "
            "exactly three above it"
        )
    chosen, (lowered, over) = choice

    prices: list[CeilingPrice] = []
    for formulation, brands in formulations:
        molecule, strength, unit = formulation
        highest = max(brand.mrp for brand in brands)
        base = (highest / (1 + brands[0].gst_rate)).quantize(PAISA, rounding=ROUND_CEILING)
        prices.append(
            CeilingPrice(molecule, strength, unit, base, FIRST_NOTIFIED, "SIM/NPPA/2023/CEILINGS")
        )
        if formulation == chosen:
            prices.append(lowered)
        else:
            revised = (base * (1 + WPI_CHANGE_2026)).quantize(PAISA, rounding=ROUND_HALF_UP)
            prices.append(
                CeilingPrice(molecule, strength, unit, revised, REVISION_DAY, "SIM/NPPA/2026/WPI")
            )

    rise_batch, units_sold = None, 0
    days = [ist_date(m.at) for m in business.ledger]
    sold_by = rise_received + timedelta(days=RISE_SELLING_DAYS)
    if days and min(days) <= rise_received and sold_by <= max(days):
        rise_batch, units_sold = _seed_price_rise(business, rise, rise_received, seed)
    return PriceScenario(
        ceilings=CeilingTable(prices),
        lowered=lowered,
        over_ceiling=over,
        price_rise=rise_batch,
        price_rise_units_sold=units_sold,
    )


def _lower_below_three(
    formulation: tuple[str, str, str], brands: list[Item]
) -> tuple[CeilingPrice, frozenset[str]] | None:
    """A ceiling that exactly the three dearest brands' MRPs exceed, and those brands, if any."""
    by_price = sorted(brands, key=lambda brand: (-brand.mrp, brand.id))
    if len(by_price) <= BRANDS_OVER_CEILING:
        return None
    gst_rate = brands[0].gst_rate
    first_compliant = by_price[BRANDS_OVER_CEILING].mrp
    ceiling = (first_compliant / (1 + gst_rate)).quantize(PAISA, rounding=ROUND_CEILING)
    lowered = CeilingPrice(*formulation, ceiling, REVISION_DAY, "SIM/NPPA/2026/017")
    # Rounding the ceiling up can lift its limit with GST a paisa above the first compliant
    # brand, onto a dearer brand priced a paisa more; the paisa below may still allow it.
    if ceiling > PAISA:
        lower = replace(lowered, ceiling=ceiling - PAISA)
        if lower.max_retail_price(gst_rate) >= first_compliant:
            lowered = lower
    limit = lowered.max_retail_price(gst_rate)
    over = frozenset(brand.id for brand in brands if brand.mrp > limit)
    return (lowered, over) if len(over) == BRANDS_OVER_CEILING else None


def _seed_price_rise(
    business: Business, rise: Decimal, received: date, seed: int
) -> tuple[BatchKey, int]:
    manufactured = received - timedelta(days=18)
    window_start = manufactured - timedelta(days=365)
    made_in_window = {
        key.item_id for key, batch in business.batches.items()
        if window_start <= batch.manufactured < manufactured
    }  # fmt: skip
    item = next(
        (
            item
            for item in sorted(business.catalogue.items, key=lambda i: i.id)
            if not item.dpco_scheduled and item.id in made_in_window
        ),
        None,
    )
    if item is None:
        raise LookupError("no non-scheduled item has a batch made in the year before the rise")

    mrp = (item.mrp * (1 + rise)).quantize(PAISA, rounding=ROUND_HALF_UP)
    key = BatchKey(item.company_id, item.id, "PR2601", date(2027, 12, 31))
    if key in business.batches:
        raise ValueError(f"batch {key.batch_no} is already in the business")
    business.batches[key] = Batch(key=key, manufactured=manufactured, mrp=mrp)

    rng = random.Random(seed)
    location = location_for(item)
    gst = item.gst_rate

    def record(number, kind, day, qty, document, party=None, rate=None):
        business.ledger.append(
            StockMovement(
                id=f"PRICE-{key.batch_no}-{number:03d}",
                at=ist_datetime(day, time(12, 0)),
                kind=kind,
                batch=key,
                location_id=location,
                qty=qty,
                document_ref=document,
                party_id=party,
                rate=rate,
            )
        )

    record(
        0,
        MovementType.PURCHASE,
        received,
        200,
        f"PO-{received:%y%m%d}-PR",
        party=item.company_id,
        rate=price_to_stockist(mrp, gst),
    )
    chemists = rng.sample([c.id for c in business.chemists], min(6, len(business.chemists)))
    sold = 0
    for number, chemist in enumerate(chemists, start=1):
        qty = rng.randint(5, 20)
        day = received + timedelta(days=rng.randint(3, RISE_SELLING_DAYS))
        record(
            number,
            MovementType.SALE,
            day,
            -qty,
            f"INV-{day:%y%m%d}-P{number:02d}",
            party=chemist,
            rate=price_to_retailer(mrp, gst),
        )
        sold += qty
    return key, sold
