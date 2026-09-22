"""Scripted events layered onto a simulated business.

A scenario gives tests and demos a known right answer. The seeded recall is
the one the recall drill must reproduce exactly: batch AZ4021 of an
azithromycin brand, received in January 2026, supplied to 38 chemists, with 210
strips still on hand when the Class I recall notice arrives. Around it, the
recall drill adds the notice itself, the returns that follow, and batches built
to be mistaken for it.
"""

from __future__ import annotations

import calendar
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from batchward.compliance.recall import RecallClass, RecallNotice
from batchward.core.clock import ist_datetime
from batchward.core.models import Batch, BatchKey, MovementType, StockMovement
from batchward.core.trace import trace_batch
from batchward.sim.business import (
    RETURNS,
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
        party_id=item.company_id,
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


def recall_notice(
    business: Business,
    scenario: RecallScenario,
    *,
    received_at: datetime | None = None,
    reference: str = "RN/2026/014",
) -> RecallNotice:
    """The manufacturer's recall letter for the seeded batch, written the way such letters
    name things: the manufacturer with its "M/s" and address, the product by molecule and
    strength rather than brand."""
    item = next(i for i in business.catalogue.items if i.id == scenario.batch.item_id)
    company = next(c for c in business.catalogue.companies if c.id == scenario.batch.company_id)
    return RecallNotice(
        reference=reference,
        source=company.name,
        recall_class=RecallClass(scenario.recall_class),
        received_at=received_at or ist_datetime(scenario.notice_date, time(9, 15)),
        batch_no=scenario.batch.batch_no,
        manufacturer=f"M/s. {company.name} Ltd., {company.address or 'India'}",
        product=f"{item.molecule} Tablets IP {item.strength}",
        expiry=scenario.batch.expiry,
    )


@dataclass(frozen=True, slots=True)
class RecallReturns:
    prompt: frozenset[str]
    """Chemists who returned everything within 72 hours of the notice."""
    late: frozenset[str]
    """Chemists who returned everything, but after 72 hours."""
    never: frozenset[str]
    """Chemists who returned nothing."""


def seed_recall_returns(
    business: Business,
    scenario: RecallScenario,
    *,
    notice_received: datetime,
    late: int = 4,
    never: int = 2,
    seed: int = 4022,
) -> RecallReturns:
    """Chemists send the recalled batch back onto the breakage and expiry shelf.

    Most return every strip within 72 hours; ``late`` take up to ten days; ``never``
    send nothing back, so a report dated afterwards has something to chase.
    """
    if late + never > len(scenario.chemists):
        raise ValueError("more late and missing chemists than chemists supplied")
    rng = random.Random(seed)
    key = scenario.batch
    at_notice = trace_batch(business.ledger, key, as_of=notice_received)
    held = {recipient.party_id: recipient.units for recipient in at_notice.recipients}
    chemists = sorted(scenario.chemists)
    rng.shuffle(chemists)
    never_ = frozenset(chemists[:never])
    late_ = frozenset(chemists[never : never + late])
    prompt = frozenset(chemists[never + late :])

    item = next(i for i in business.catalogue.items if i.id == key.item_id)
    rate = price_to_retailer(item.mrp, item.gst_rate)
    returns = []
    for chemist in sorted((prompt | late_) & held.keys()):
        hours = (2, 70) if chemist in prompt else (80, 230)
        delay = timedelta(minutes=rng.randint(hours[0] * 60, hours[1] * 60))
        returns.append((notice_received + delay, chemist))
    returns.sort()
    for number, (when, chemist) in enumerate(returns, start=1):
        business.ledger.append(
            StockMovement(
                id=f"RCL-{key.batch_no}-R{number:03d}",
                at=when,
                kind=MovementType.SALE_RETURN,
                batch=key,
                location_id=RETURNS.id,
                qty=held[chemist],
                document_ref=f"CN-{when:%y%m%d}-R{number:03d}",
                party_id=chemist,
                rate=rate,
            )
        )
    return RecallReturns(prompt=prompt, late=late_, never=never_)


def seed_recall_look_alikes(business: Business, scenario: RecallScenario) -> tuple[BatchKey, ...]:
    """Batches a careless match would freeze along with the recalled one.

    - the same product with batch AZ4O21 (letter O), easily misread as AZ4021;
    - the same product and batch number from an earlier year, expiring a year sooner;
    - another manufacturer's azithromycin carrying the same batch number and expiry.

    None of them is recalled; each must be raised for review and none blocked.
    """
    key = scenario.batch
    items = business.catalogue.items
    item = next(i for i in items if i.id == key.item_id)
    rival = next(
        (i for i in items if i.molecule == item.molecule and i.company_id != item.company_id),
        None,
    )
    if rival is None:
        raise LookupError(f"no other manufacturer's {item.molecule} to build a look-alike from")
    year_sooner = key.expiry.replace(
        year=key.expiry.year - 1,
        day=calendar.monthrange(key.expiry.year - 1, key.expiry.month)[1],
    )
    look_alikes = (
        (item, key.batch_no.replace("0", "O", 1), key.expiry, date(2025, 12, 1)),
        (item, key.batch_no, year_sooner, date(2025, 1, 10)),
        (rival, key.batch_no, key.expiry, date(2025, 12, 15)),
    )
    keys = []
    for number, (holder, batch_no, expiry, bought) in enumerate(look_alikes, start=1):
        decoy = BatchKey(holder.company_id, holder.id, batch_no, expiry)
        if decoy in business.batches or decoy == key:
            raise ValueError(f"batch {decoy} already exists")
        business.batches[decoy] = Batch(
            key=decoy, manufactured=bought - timedelta(days=60), mrp=holder.mrp
        )
        business.ledger.append(
            StockMovement(
                id=f"RCL-{key.batch_no}-D{number}",
                at=ist_datetime(bought, time(11, 30)),
                kind=MovementType.PURCHASE,
                batch=decoy,
                location_id=location_for(holder),
                qty=150,
                document_ref=f"PO-{bought:%y%m%d}-D{number}",
                party_id=holder.company_id,
                rate=price_to_stockist(holder.mrp, holder.gst_rate),
            )
        )
        keys.append(decoy)
    return tuple(keys)


def _split(rng: random.Random, total: int, parts: int, minimum: int) -> list[int]:
    """Split ``total`` into ``parts`` random shares of at least ``minimum`` each."""
    shares = [minimum] * parts
    for _ in range(total - minimum * parts):
        shares[rng.randrange(parts)] += 1
    return shares
