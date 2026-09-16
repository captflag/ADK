"""Scripted events layered onto a simulated business.

A scenario gives tests and demos a known right answer. The seeded recall is
the one the recall drill must reproduce exactly: batch AZ4021 of an
azithromycin brand, received in January 2026, supplied to 38 chemists, with 210
strips still on hand when the Class I recall notice arrives.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal

from batchward.core.clock import ist_datetime
from batchward.core.models import Batch, BatchKey, MovementType, StockMovement
from batchward.sim.business import (
    Business,
    location_for,
    price_to_retailer,
    price_to_stockist,
)


@dataclass(frozen=True, slots=True)
class RecallScenario:
    batch: BatchKey
    recall_class: str
    notice_date: date
    chemists: frozenset[str]
    """Every chemist the batch was supplied to."""
    units_received: int
    units_supplied: int
    units_on_hand: int


def seed_recall(
    business: Business,
    *,
    molecule: str = "Azithromycin",
    batch_no: str = "AZ4021",
    expiry: date = date(2027, 10, 31),
    received: date = date(2026, 1, 5),
    notice: date = date(2026, 2, 12),
    n_chemists: int = 38,
    units_received: int = 1_000,
    units_on_hand: int = 210,
    min_units_each: int = 5,
    seed: int = 4021,
) -> RecallScenario:
    """Add a batch to the business, sell it to ``n_chemists`` chemists, and describe the recall."""
    item = next((i for i in business.catalogue.items if i.molecule == molecule), None)
    if item is None:
        raise LookupError(f"the catalogue has no {molecule} brand to recall")
    if n_chemists > len(business.chemists):
        raise ValueError(f"the business has only {len(business.chemists)} chemists")
    units_supplied = units_received - units_on_hand
    if units_supplied < n_chemists * min_units_each:
        raise ValueError("not enough supplied units to give every chemist the minimum")
    if (notice - received).days < 3:
        raise ValueError("the notice must come at least three days after receipt")

    key = BatchKey(company_id=item.company_id, item_id=item.id, batch_no=batch_no, expiry=expiry)
    if key in business.batches:
        raise ValueError(f"batch {batch_no} is already in the business")
    business.batches[key] = Batch(key=key, manufactured=date(2025, 11, 1), mrp=item.mrp)

    rng = random.Random(seed)
    location_id = location_for(item)
    chemists = rng.sample([c.id for c in business.chemists], n_chemists)
    shares = _split(rng, units_supplied, n_chemists, min_units_each)
    ids = iter(range(1, 10_000))

    def record(kind, when, qty, document_ref, party_id=None, rate=None):
        business.ledger.append(
            StockMovement(
                id=f"RCL-{batch_no}-{next(ids):04d}",
                at=when,
                kind=kind,
                batch=key,
                location_id=location_id,
                qty=qty,
                document_ref=document_ref,
                party_id=party_id,
                rate=rate,
            )
        )

    record(
        MovementType.PURCHASE,
        ist_datetime(received, time(11, 0)),
        units_received,
        f"PO-{received:%y%m%d}-{batch_no}",
        rate=price_to_stockist(item.mrp, item.gst_rate),
    )

    selling_days = (notice - received).days - 2
    sales = sorted(
        (
            received + timedelta(days=rng.randint(1, selling_days)),
            rng.randint(10 * 60, 19 * 60 + 30),
            chemist,
            qty,
        )
        for chemist, qty in zip(chemists, shares, strict=True)
    )
    rate: Decimal = price_to_retailer(item.mrp, item.gst_rate)
    for number, (day, minute, chemist, qty) in enumerate(sales, start=1):
        record(
            MovementType.SALE,
            ist_datetime(day, time(minute // 60, minute % 60)),
            -qty,
            f"INV-{day:%y%m%d}-R{number:03d}",
            party_id=chemist,
            rate=rate,
        )

    return RecallScenario(
        batch=key,
        recall_class="I",
        notice_date=notice,
        chemists=frozenset(chemists),
        units_received=units_received,
        units_supplied=units_supplied,
        units_on_hand=units_on_hand,
    )


def _split(rng: random.Random, total: int, parts: int, minimum: int) -> list[int]:
    """Split ``total`` into ``parts`` random shares of at least ``minimum`` each."""
    shares = [minimum] * parts
    for _ in range(total - minimum * parts):
        shares[rng.randrange(parts)] += 1
    return shares
