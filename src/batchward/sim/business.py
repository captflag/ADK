"""Day-by-day trading for a simulated pharma stockist.

Each simulated day: expired stock is written off, purchase orders due that day
arrive as fresh batches, companies on their weekly ordering day are topped up,
and chemists' orders are filled first-expiry-first-out from whatever is
sellable. Everything is recorded in an ordinary ledger, so the rest of the
system cannot tell simulated history from real history.

Chemist names use real Nagpur localities with generated shop names; they, and
the licence numbers, are fictional.
"""

from __future__ import annotations

import calendar
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from itertools import count

from batchward.core.clock import ist_datetime
from batchward.core.fefo import allocate_fefo, sellable_stock
from batchward.core.ledger import Ledger
from batchward.core.models import (
    Batch,
    BatchKey,
    Item,
    Location,
    MovementType,
    Party,
    PartyKind,
    StockMovement,
)
from batchward.sim.catalogue import Catalogue, Therapy, build_catalogue

GODOWN = Location(id="GODOWN", name="Main godown")
COLD_ROOM = Location(id="COLD_ROOM", name="Cold room", cold_room=True)
RETURNS = Location(id="RETURNS", name="Breakage and expiry shelf")
"""Where chemists' near-expiry returns wait to be claimed or written off; never sold from."""

RETAILER_MARGIN = Decimal("0.20")
STOCKIST_MARGIN = Decimal("0.10")

_SEASONS: dict[Therapy, dict[int, float]] = {
    Therapy.ANTI_INFECTIVE: {7: 1.5, 8: 1.6, 9: 1.5, 10: 1.2, 12: 1.2, 1: 1.2},
    Therapy.ACUTE: {4: 1.2, 5: 1.3, 6: 1.3, 7: 1.4, 8: 1.4, 9: 1.3},
    Therapy.RESPIRATORY: {11: 1.4, 12: 1.6, 1: 1.6, 2: 1.3},
}
_SUNDAY_FACTOR = 0.3

_SURNAMES = (
    "Agarwal", "Sharma", "Deshmukh", "Joshi", "Patil", "Khan", "Gupta", "Jain", "Kulkarni",
    "Shaikh", "Wankhede", "Rathi", "Bhoyar", "Thakre", "Mehta", "Chandak", "Lohia", "Kothari",
    "Mishra", "Tiwari",
)  # fmt: skip
_SHOP_WORDS = ("Medical", "Medicos", "Pharmacy", "Chemists", "Medical Stores", "Drug House")
_LOCALITIES = (
    "Sitabuldi", "Dharampeth", "Sadar", "Itwari", "Mahal", "Manish Nagar", "Pratap Nagar",
    "Wardhaman Nagar", "Hingna", "Kamptee Road",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class SimConfig:
    start: date
    days: int
    seed: int = 42
    n_chemists: int = 380
    cover_days: int = 21
    """Days of expected demand each purchase order tops stock up to."""
    lead_time_days: tuple[int, int] = (2, 5)
    opening_shelf_life_months: tuple[int, int] = (6, 30)
    """Months left before expiry on the stock held when the simulation starts."""
    scheme_overbuy_chance: float = 0.05
    """Chance a purchase order is tripled to chase a company scheme."""
    short_dated_chance: float = 0.06
    """Chance a delivery arrives with only 3 to 9 months left, as companies clear old stock."""
    demand_collapse_chance: float = 0.2
    """Chance an item's demand falls to 5-30% partway through, as when a brand loses to a rival."""
    demand_memory_days: int = 56
    """How much sales history the owner reorders from, so demand shifts show up late."""
    case_sizes: tuple[int, ...] = (10, 25, 50)
    """Units per shipping case; companies supply whole cases only."""
    near_expiry_return_days: int = 90
    """How close to expiry chemists send back stock they cannot sell."""
    near_expiry_return_share: float = 0.4
    """Share of a batch a chemist still holds that they return as it nears expiry."""


@dataclass(slots=True)
class Business:
    catalogue: Catalogue
    chemists: tuple[Party, ...]
    locations: tuple[Location, ...]
    ledger: Ledger
    batches: dict[BatchKey, Batch]
    unmet_demand: dict[str, int]
    """Units ordered but not supplied, by item id: the demand a stockout hides."""


def simulate(config: SimConfig, catalogue: Catalogue | None = None) -> Business:
    return _Simulation(config, catalogue or build_catalogue(config.seed)).run()


def location_for(item: Item) -> str:
    return COLD_ROOM.id if item.cold_chain else GODOWN.id


def seasonal_factor(therapy: Therapy, month: int) -> float:
    return _SEASONS.get(therapy, {}).get(month, 1.0)


def price_to_retailer(mrp: Decimal, gst_rate: Decimal) -> Decimal:
    return (mrp * (1 - RETAILER_MARGIN) / (1 + gst_rate)).quantize(Decimal("0.01"))


def price_to_stockist(mrp: Decimal, gst_rate: Decimal) -> Decimal:
    return (price_to_retailer(mrp, gst_rate) * (1 - STOCKIST_MARGIN)).quantize(Decimal("0.01"))


class _Simulation:
    def __init__(self, config: SimConfig, catalogue: Catalogue) -> None:
        self.config = config
        self.catalogue = catalogue
        self.rng = random.Random(config.seed)
        self.ledger = Ledger()
        self.batches: dict[BatchKey, Batch] = {}
        self.items = {item.id: item for item in catalogue.items}
        self.items_by_company: defaultdict[str, list[Item]] = defaultdict(list)
        for item in catalogue.items:
            self.items_by_company[item.company_id].append(item)
        self.chemists = _make_chemists(self.rng, config.n_chemists)
        # A long tail of slow movers; mu = -sigma^2 / 2 keeps mean popularity at 1.
        self.popularity = {item.id: self.rng.lognormvariate(-0.4, 0.9) for item in catalogue.items}
        self.case_size = {item.id: self.rng.choice(config.case_sizes) for item in catalogue.items}
        self.sales_rate = {item.id: self._base_demand(item) for item in catalogue.items}
        """What the owner believes each item sells per day, learned from recent sales."""
        self.collapses: dict[str, tuple[date, float]] = {}
        for item in catalogue.items:
            if self.rng.random() < config.demand_collapse_chance:
                day = config.start + timedelta(days=self.rng.randrange(max(1, config.days)))
                self.collapses[item.id] = (day, self.rng.uniform(0.05, 0.3))
        self.arrivals: defaultdict[date, list[tuple[Item, int]]] = defaultdict(list)
        self.on_order: defaultdict[str, int] = defaultdict(int)
        self.expiring: defaultdict[date, list[BatchKey]] = defaultdict(list)
        self.return_due: defaultdict[date, list[BatchKey]] = defaultdict(list)
        self.unmet: defaultdict[str, int] = defaultdict(int)
        self._movement_ids = count(1)
        self._document_ids = count(1)

    def run(self) -> Business:
        start = self.config.start
        self._open(start)
        for offset in range(self.config.days):
            today = start + timedelta(days=offset)
            self._write_off_expired(today)
            self._take_back_near_expiry(today)
            self._receive(today)
            self._reorder(today)
            self._trade(today)
        return Business(
            catalogue=self.catalogue,
            chemists=self.chemists,
            locations=(GODOWN, COLD_ROOM, RETURNS),
            ledger=self.ledger,
            batches=self.batches,
            unmet_demand=dict(self.unmet),
        )

    # -- daily steps -----------------------------------------------------------

    def _open(self, day: date) -> None:
        low, high = self.config.opening_shelf_life_months
        for item in self.catalogue.items:
            qty = _round_up(
                math.ceil(self._base_demand(item) * self.config.cover_days),
                self.case_size[item.id],
            )
            key = self._new_batch(item, day, remaining_months=self.rng.randint(low, high))
            self._record(
                MovementType.PURCHASE,
                ist_datetime(day, time(0, 1)),
                key,
                qty,
                "OPENING",
                rate=price_to_stockist(item.mrp, item.gst_rate),
            )

    def _write_off_expired(self, today: date) -> None:
        for key in self.expiring.pop(today, ()):
            for location_id in (location_for(self.items[key.item_id]), RETURNS.id):
                remaining = self.ledger.balance(key, location_id)
                if remaining > 0:
                    self._record(
                        MovementType.WRITE_OFF,
                        ist_datetime(today, time(0, 5)),
                        key,
                        -remaining,
                        f"EXP-{today:%y%m%d}",
                        location_id=location_id,
                    )

    def _take_back_near_expiry(self, today: date) -> None:
        """Chemists return part of what they still hold of a batch nearing expiry."""
        window = timedelta(days=self.config.near_expiry_return_days)
        since = ist_datetime(today - window, time(0, 0))
        for key in self.return_due.pop(today, ()):
            held: defaultdict[str, int] = defaultdict(int)
            for m in self.ledger.movements_for(key):
                if m.party_id is None or m.at < since:
                    continue
                if m.kind in (MovementType.SALE, MovementType.SALE_RETURN):
                    held[m.party_id] -= m.qty
            item = self.items[key.item_id]
            document = f"SR-{today:%y%m%d}-{next(self._document_ids):05d}"
            for party_id in sorted(held):
                returned = math.floor(held[party_id] * self.config.near_expiry_return_share)
                if returned > 0:
                    self._record(
                        MovementType.SALE_RETURN,
                        ist_datetime(today, time(9, 30)),
                        key,
                        returned,
                        document,
                        party_id=party_id,
                        rate=price_to_retailer(item.mrp, item.gst_rate),
                        location_id=RETURNS.id,
                    )

    def _receive(self, today: date) -> None:
        for item, qty in self.arrivals.pop(today, ()):
            short_dated = self.rng.random() < self.config.short_dated_chance
            remaining_months = self.rng.randint(3, 9) if short_dated else None
            key = self._new_batch(item, today, remaining_months=remaining_months)
            self._record(
                MovementType.PURCHASE,
                ist_datetime(today, time(9, 0)),
                key,
                qty,
                f"PO-{today:%y%m%d}-{next(self._document_ids):05d}",
                rate=price_to_stockist(item.mrp, item.gst_rate),
            )
            self.on_order[item.id] -= qty

    def _reorder(self, today: date) -> None:
        for company in self.catalogue.companies:
            if today.weekday() != int(company.id[1:]) % 6:
                continue
            for item in self.items_by_company[company.id]:
                target = math.ceil(self.sales_rate[item.id] * self.config.cover_days)
                position = self._sellable_units(item, today) + self.on_order[item.id]
                if position >= target / 2:
                    continue
                qty = _round_up(target - position, self.case_size[item.id])
                if self.rng.random() < self.config.scheme_overbuy_chance:
                    qty *= 3
                arrival = today + timedelta(days=self.rng.randint(*self.config.lead_time_days))
                self.arrivals[arrival].append((item, qty))
                self.on_order[item.id] += qty

    def _trade(self, today: date) -> None:
        orders: list[tuple[int, str, int, str]] = []
        for item in self.catalogue.items:
            mean = self._expected_demand(item, today)
            demand = _poisson(self.rng, mean)
            while demand > 0:
                size = min(demand, self.rng.randint(1, max(1, math.ceil(mean))))
                minute = self.rng.randint(10 * 60, 19 * 60 + 30)
                orders.append((minute, item.id, size, self.rng.choice(self.chemists).id))
                demand -= size

        sold: defaultdict[str, int] = defaultdict(int)
        for minute, item_id, size, chemist_id in sorted(orders):
            item = self.items[item_id]
            fill = min(size, self._sellable_units(item, today))
            self.unmet[item_id] += size - fill
            sold[item_id] += fill
            if fill == 0:
                continue
            when = ist_datetime(today, time(minute // 60, minute % 60))
            document = f"INV-{today:%y%m%d}-{next(self._document_ids):05d}"
            rate = price_to_retailer(item.mrp, item.gst_rate)
            for key, qty in allocate_fefo(
                self.ledger,
                self.batches,
                item_id=item_id,
                location_id=location_for(item),
                qty=fill,
                on=today,
            ):
                self._record(
                    MovementType.SALE, when, key, -qty, document, party_id=chemist_id, rate=rate
                )

        # The owner's sense of demand is an exponential average of what actually sold.
        # Stockouts pull it down, and a collapse in demand takes weeks to register.
        smoothing = 2 / (self.config.demand_memory_days + 1)
        for item_id, rate_now in self.sales_rate.items():
            self.sales_rate[item_id] = rate_now + smoothing * (sold[item_id] - rate_now)

    # -- helpers ---------------------------------------------------------------

    def _base_demand(self, item: Item) -> float:
        return self.catalogue.molecules[item.id].daily_demand * self.popularity[item.id]

    def _expected_demand(self, item: Item, day: date) -> float:
        therapy = self.catalogue.molecules[item.id].therapy
        weekday = _SUNDAY_FACTOR if day.weekday() == 6 else 1.0
        collapse_day, remaining_share = self.collapses.get(item.id, (date.max, 1.0))
        share = remaining_share if day >= collapse_day else 1.0
        return self._base_demand(item) * seasonal_factor(therapy, day.month) * weekday * share

    def _sellable_units(self, item: Item, day: date) -> int:
        return sum(
            qty
            for _, qty in sellable_stock(
                self.ledger,
                self.batches,
                item_id=item.id,
                location_id=location_for(item),
                on=day,
            )
        )

    def _new_batch(
        self, item: Item, arrival: date, remaining_months: int | None = None
    ) -> BatchKey:
        shelf_life = self.catalogue.molecules[item.id].shelf_life_months
        if remaining_months is None:
            manufactured = arrival - timedelta(days=self.rng.randint(30, 180))
            expiry = _month_end(manufactured, shelf_life)
        else:
            expiry = _month_end(arrival, remaining_months)
            manufactured = min(_month_end(expiry, -shelf_life), arrival - timedelta(days=1))
        prefix = "".join(ch for ch in item.brand.upper() if ch.isalpha())[:2]
        while True:
            key = BatchKey(
                company_id=item.company_id,
                item_id=item.id,
                batch_no=f"{prefix}{self.rng.randint(1000, 9999)}",
                expiry=expiry,
            )
            if key not in self.batches:
                break
        self.batches[key] = Batch(key=key, manufactured=manufactured, mrp=item.mrp)
        self.expiring[expiry].append(key)
        self.return_due[expiry - timedelta(days=self.config.near_expiry_return_days)].append(key)
        return key

    def _record(
        self,
        kind: MovementType,
        when: datetime,
        key: BatchKey,
        qty: int,
        document_ref: str,
        *,
        party_id: str | None = None,
        rate: Decimal | None = None,
        location_id: str | None = None,
    ) -> None:
        self.ledger.append(
            StockMovement(
                id=f"SIM{next(self._movement_ids):08d}",
                at=when,
                kind=kind,
                batch=key,
                location_id=location_id or location_for(self.items[key.item_id]),
                qty=qty,
                document_ref=document_ref,
                party_id=party_id,
                rate=rate,
            )
        )


def _make_chemists(rng: random.Random, n: int) -> tuple[Party, ...]:
    names = [
        f"{surname} {shop}, {locality}"
        for surname in _SURNAMES
        for shop in _SHOP_WORDS
        for locality in _LOCALITIES
    ]
    if not 1 <= n <= len(names):
        raise ValueError(f"n_chemists must lie within 1..{len(names)}")
    return tuple(
        Party(
            id=f"CH{index:03d}",
            kind=PartyKind.CHEMIST,
            name=name,
            drug_licence_no=f"NGP/20B/{rng.randint(10000, 99999)}",
        )
        for index, name in enumerate(rng.sample(names, n), start=1)
    )


def _poisson(rng: random.Random, mean: float) -> int:
    if mean <= 0:
        return 0
    if mean > 60:  # normal approximation keeps large means fast
        return max(0, round(rng.gauss(mean, math.sqrt(mean))))
    threshold, k, product = math.exp(-mean), 0, rng.random()
    while product > threshold:
        k += 1
        product *= rng.random()
    return k


def _month_end(day: date, months: int) -> date:
    """The last day of the month ``months`` after ``day``, as printed pharma expiries are."""
    year, month_index = divmod(day.month - 1 + months, 12)
    year += day.year
    month = month_index + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _round_up(qty: int, step: int) -> int:
    return max(step, math.ceil(qty / step) * step)
