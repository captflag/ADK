"""Tools the agents call. Every figure an agent states comes from one of these (ADR 0003).

Each tool is a plain function: its type hints and docstring are what the model
sees, so both are written for the model as much as for a person. Results are
JSON-safe dictionaries. Money is given twice — formatted in Indian digit
grouping for quoting, and as a number for comparison — and dates as ISO
strings. A tool that cannot answer says why in an ``error`` field rather than
returning something plausible.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from datetime import time, timedelta
from decimal import Decimal

from batchward.agents.data import StockData, current, records_path
from batchward.analysis.ageing import dead_stock
from batchward.analysis.costs import batch_costs
from batchward.analysis.demand import aggregate, daily_sales
from batchward.analysis.expiry import expiry_exposure
from batchward.analysis.health import stock_health
from batchward.analysis.routing import daily_rates, forecast_weekly
from batchward.bridge.marg_layout import format_expiry
from batchward.buying.order import OPEN_DAYS, OpenOrder, still_due
from batchward.buying.suggest import COVER_DAYS, LEAD_DAYS, suggest
from batchward.claims.claim import settled
from batchward.claims.submission import windows_for
from batchward.claims.windows import CLOSING_DAYS, WindowState, lost_claims
from batchward.compliance.prices import (
    Cause,
    CeilingTable,
    PriceCheck,
    Verdict,
    check_batch_price,
    overcharge_exposure,
    price_rises,
)
from batchward.compliance.recall_report import RecallReport
from batchward.compliance.registrar import keep_from, rule65_check
from batchward.core.clock import ist_date, ist_datetime
from batchward.core.holds import Hold, HoldLog
from batchward.core.trace import trace_batch
from batchward.records import recalls
from batchward.records.store import RecordsError, RecordStore
from batchward.reporting.inr import format_inr
from batchward.reporting.recall import format_moment

MAX_LIMIT = 50


def _money(amount: Decimal | None) -> dict[str, str | float | None]:
    if amount is None:
        return {"formatted": "cost unknown", "rupees": None}
    return {"formatted": format_inr(amount), "rupees": float(amount)}


def _limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


def stock_health_summary() -> dict:
    """Summarise stock health today: total stock value at cost, how that value splits by age,
    dead stock (items with no sale for over 120 days), stock at risk of expiring within 180
    days, and consumption over the last year by ABC class. Use this first for any general
    question about the state of stock, and for the morning brief."""
    data = current()
    health = stock_health(data.ledger, data.locations, on=data.today)
    return {
        "as_of": data.today.isoformat(),
        "stock_value": _money(health.stock_value),
        "units_without_known_cost": health.unvalued_units,
        "stock_value_by_age": {bucket: _money(v) for bucket, v in health.value_by_age.items()},
        "dead_stock": {
            "items": len(health.dead_stock),
            "value": _money(health.dead_stock_value),
            "rule": "sellable stock with no sale for more than 120 days",
        },
        "expiry_risk": {
            "batches": len(health.expiry_risks),
            "value": _money(health.expiry_value_at_risk),
            "rule": (
                "batches expiring within 180 days that will not sell in time at the forecast rate"
            ),
        },
        "consumption_last_365_days_by_class": {
            str(value_class): {
                "items": health.items_by_class[value_class],
                "value": _money(value),
            }
            for value_class, value in health.consumption_by_class.items()
        },
    }


def list_dead_stock(limit: int = 10) -> dict:
    """List items with sellable stock that has not sold for more than 120 days, largest value
    first. Each entry gives the brand, units on hand, value at cost, the last sale date and how
    many days the item has been idle. `limit` is how many items to return (at most 50)."""
    data = current()
    costs = batch_costs(data.ledger)
    dead = dead_stock(data.ledger, costs, data.locations, on=data.today)
    return {
        "as_of": data.today.isoformat(),
        "total_items": len(dead),
        "items": [
            {
                "item_id": d.item_id,
                "brand": data.items[d.item_id].brand,
                "units": d.units,
                "value": _money(d.value),
                "units_without_known_cost": d.unvalued_units,
                "last_sold": d.last_sold.isoformat() if d.last_sold else None,
                "idle_days": d.idle_days,
            }
            for d in dead[: _limit(limit)]
        ],
    }


def list_expiry_risks(within_days: int = 180, limit: int = 10) -> dict:
    """List batches whose stock is likely to expire before it sells, largest value at risk
    first. Covers batches expiring within `within_days`, stock already expired, and stock on
    shelves it is never sold from. Each entry gives the brand, batch number, printed expiry,
    units held, units at risk, value at risk and the reason. `limit` is at most 50."""
    data = current()
    costs = batch_costs(data.ledger)
    rates = daily_rates(data.ledger, on=data.today)
    risks = expiry_exposure(
        data.ledger, costs, data.locations, rates, on=data.today, within_days=max(1, within_days)
    )
    return {
        "as_of": data.today.isoformat(),
        "total_batches": len(risks),
        "total_value_at_risk": _money(
            sum((r.value_at_risk or Decimal(0) for r in risks), Decimal(0))
        ),
        "batches": [
            {
                "brand": data.items[r.batch.item_id].brand,
                "batch_no": r.batch.batch_no,
                "expiry": format_expiry(r.batch.expiry),
                "days_to_expiry": r.days_to_expiry,
                "location": r.location_id,
                "units": r.units,
                "units_at_risk": r.units_at_risk,
                "value_at_risk": _money(r.value_at_risk),
                "reason": str(r.reason),
            }
            for r in risks[: _limit(limit)]
        ],
    }


def find_items(query: str, limit: int = 10) -> dict:
    """Find items whose brand name or molecule contains `query` (case-insensitive), for example
    "azithro" or "Azinil". Returns each match's item_id, brand, molecule, strength, pack, company
    and units on hand. Use this to turn a name the user mentions into an item_id."""
    data = current()
    needle = query.strip().lower()
    if not needle:
        return {"error": "give part of a brand or molecule name to search for"}
    on_hand: defaultdict[str, int] = defaultdict(int)
    for (key, _location), units in data.ledger.balances().items():
        on_hand[key.item_id] += units
    matches = [
        item
        for item in data.items.values()
        if needle in item.brand.lower() or needle in item.molecule.lower()
    ]
    matches.sort(key=lambda item: (item.brand, item.id))
    return {
        "query": query,
        "total_matches": len(matches),
        "items": [
            {
                "item_id": item.id,
                "brand": item.brand,
                "molecule": item.molecule,
                "strength": item.strength,
                "pack": item.unit,
                "company": data.parties[item.company_id].name
                if item.company_id in data.parties
                else item.company_id,
                "units_on_hand": on_hand.get(item.id, 0),
            }
            for item in matches[: _limit(limit)]
        ],
    }


def item_stock(item_id: str) -> dict:
    """Show every batch of one item on hand today: batch number, printed expiry, days to
    expiry, location and units, earliest expiry first. Needs an item_id from find_items."""
    data = current()
    item = data.items.get(item_id)
    if item is None:
        return {"error": f"no item with id {item_id!r}; use find_items to look it up"}
    costs = batch_costs(data.ledger)
    batches = []
    for location_id in sorted(location.id for location in data.locations):
        for key, units in data.ledger.stock_of_item(item_id, location_id).items():
            batches.append((key, location_id, units))
    batches.sort(key=lambda b: (b[0].expiry, b[0].batch_no, b[1]))
    sellable = {location.id for location in data.locations if location.sellable}
    return {
        "as_of": data.today.isoformat(),
        "item_id": item_id,
        "brand": item.brand,
        "batches": [
            {
                "batch_no": key.batch_no,
                "expiry": format_expiry(key.expiry),
                "days_to_expiry": (key.expiry - data.today).days,
                "location": location_id,
                "sellable_location": location_id in sellable,
                "units": units,
                "value": _money(None if key not in costs else costs[key] * units),
            }
            for key, location_id, units in batches
        ],
    }


def forecast_item(item_id: str, weeks_of_history: int = 26) -> dict:
    """Forecast next week's demand for one item from its recent weekly sales. Returns the demand
    pattern, the method used for that pattern, expected units per week and per day, units that
    can be sold (on sellable shelves, not expired and not blocked), and how many weeks that
    stock covers. Needs an item_id from find_items."""
    data = current()
    item = data.items.get(item_id)
    if item is None:
        return {"error": f"no item with id {item_id!r}; use find_items to look it up"}
    weeks = max(4, min(weeks_of_history, 104))
    start = data.today - timedelta(days=weeks * 7)
    history = daily_sales(data.ledger, start=start, end=data.today - timedelta(days=1))
    weekly = aggregate(history.get(item_id, [0] * (weeks * 7)), 7)
    forecast = forecast_weekly(weekly)
    sellable = {location.id for location in data.locations if location.sellable}
    held = _held_batches()
    on_hand = sum(
        units
        for location_id in sellable
        for key, units in data.ledger.stock_of_item(item_id, location_id).items()
        if key.expiry > data.today and key not in held
    )
    per_week = forecast.units_per_week
    return {
        "as_of": data.today.isoformat(),
        "item_id": item_id,
        "brand": item.brand,
        "weeks_of_history": weeks,
        "weekly_sales": weekly,
        "demand_pattern": str(forecast.pattern),
        "method": forecast.method,
        "forecast_units_per_week": round(per_week, 1),
        "forecast_units_per_day": round(per_week / 7, 2),
        "units_that_can_be_sold": on_hand,
        "weeks_of_cover": round(on_hand / per_week, 1) if per_week > 0 else None,
    }


def trace_batch_number(batch_no: str) -> dict:
    """Trace a batch by its printed batch number: units received, every chemist it was supplied
    to (with units, first and last supply dates and bill numbers), units still on hand by
    location, and any units sold without a recorded buyer. Use this for recalls and quality
    alerts. A batch number can match more than one product; each match is traced separately."""
    data = current()
    wanted = "".join(batch_no.split()).upper()
    keys = sorted(key for key in data.batches if key.batch_no == wanted)
    if not keys:
        return {"error": f"no batch numbered {batch_no!r} has ever been held"}
    traces = []
    for key in keys:
        trace = trace_batch(data.ledger, key)
        traces.append(
            {
                "brand": data.items[key.item_id].brand
                if key.item_id in data.items
                else key.item_id,
                "batch_no": key.batch_no,
                "expiry": format_expiry(key.expiry),
                "units_received": trace.received,
                "units_supplied": trace.supplied,
                "units_on_hand_by_location": trace.on_hand,
                "units_sold_without_buyer": trace.untraceable,
                "chemists_supplied": len(trace.recipients),
                "recipients": [
                    {
                        "party_id": r.party_id,
                        "name": data.parties[r.party_id].name
                        if r.party_id in data.parties
                        else r.party_id,
                        "drug_licence_no": data.parties[r.party_id].drug_licence_no
                        if r.party_id in data.parties
                        else None,
                        "units": r.units,
                        "first_supplied": ist_date(r.first_supplied).isoformat(),
                        "last_supplied": ist_date(r.last_supplied).isoformat(),
                        "bills": list(r.documents),
                    }
                    for r in trace.recipients
                ],
            }
        )
    return {"batch_no": wanted, "matches": traces}


def recall_status(batch_no: str) -> dict:
    """Show every recall notice recorded for a batch number: who issued it, its class and when
    it arrived; whether each batch it named was blocked, when, and whether the block was later
    lifted; the deadlines and whether they were met; units supplied to chemists, recovered and
    still outstanding, with the chemists still holding the most; and any sale after the notice.
    Also lists batches that only resemble the notice, which were raised for a person to review
    instead of being blocked. Use this first for any question about a recall."""
    data = current()
    wanted = "".join(batch_no.split()).upper()
    path = records_path()
    if path is None or not path.is_file():
        return {"error": "no recall records are available, so no recall notice can be checked"}
    start_of_today = ist_datetime(data.today, time(0, 0))
    try:
        with RecordStore(path, create=False) as store:
            statuses = [
                recalls.recall_status(
                    store,
                    notice.reference,
                    ledger=data.ledger,
                    items=data.items,
                    parties=data.parties,
                    as_of=max(start_of_today, notice.received_at),
                    batches=data.batches,
                )
                for notice in store.notices_naming(wanted)
            ]
            log = store.hold_log()
    except (RecordsError, sqlite3.Error) as error:
        return {"error": f"the recall records cannot be read: {error}"}
    if not statuses:
        return {
            "batch_no": wanted,
            "notices": [],
            "note": "no recall notice has been recorded for this batch number",
        }
    return {"batch_no": wanted, "notices": [_notice(status, log, data) for status in statuses]}


def _notice(status: recalls.RecallStatus, log: HoldLog, data: StockData) -> dict:
    notice = status.notice
    return {
        "reference": notice.reference,
        "issued_by": notice.source,
        "recall_class": str(notice.recall_class),
        "received": format_moment(notice.received_at),
        "blocked_batches": [
            _blocked_batch(report, status.holds, log, data) for report in status.reports
        ],
        "named_exactly_but_not_blocked": [
            {
                "batch_no": batch.batch_no,
                "expiry": format_expiry(batch.expiry),
                "brand": _brand(data, batch.item_id),
                "action": "a person must receive the notice again to block this batch",
            }
            for batch in status.unblocked
        ],
        "raised_for_review": [
            {
                "batch_no": candidate.batch.batch_no,
                "expiry": format_expiry(candidate.batch.expiry),
                "brand": _brand(data, candidate.batch.item_id),
                "reasons": list(candidate.reasons),
            }
            for candidate in status.review
        ],
    }


def _blocked_batch(
    report: RecallReport, holds: Iterable[Hold], log: HoldLog, data: StockData
) -> dict:
    batch = report.batch
    outstanding = [chemist for chemist in report.chemists if chemist.outstanding]
    return {
        "batch_no": batch.batch_no,
        "expiry": format_expiry(batch.expiry),
        "brand": _brand(data, batch.item_id),
        "as_of": format_moment(report.as_of),
        "holds": [_hold(hold, log) for hold in holds if hold.batch == batch],
        "deadlines": [
            {
                "deadline": deadline.name,
                "due": format_moment(deadline.due),
                "state": str(deadline.state),
            }
            for deadline in (report.stop_sale, report.completion)
            if deadline is not None
        ],
        "units_received": report.received,
        "units_on_hand_when_notice_arrived": sum(report.on_hand_at_notice.values()),
        "chemists_supplied": sum(1 for chemist in report.chemists if chemist.supplied),
        "units_supplied": report.supplied,
        "units_recovered": report.recovered,
        "units_outstanding": report.outstanding,
        "units_sold_after_notice": report.sold_after_notice,
        "units_sold_after_block_lifted": -sum(m.qty for m in report.sales_after_release),
        "units_sold_without_buyer": report.untraceable,
        "units_transferred_not_arrived": report.in_transit,
        "chemists_still_holding": len(outstanding),
        "largest_outstanding": [
            {
                "name": data.parties[c.party_id].name if c.party_id in data.parties else c.party_id,
                "units_outstanding": c.outstanding,
            }
            for c in outstanding[:10]
        ],
    }


def _hold(hold: Hold, log: HoldLog) -> dict:
    release = log.release_of(hold.id)
    return {
        "hold_id": hold.id,
        "blocked": format_moment(hold.at),
        "in_force": release is None,
        "released": None
        if release is None
        else f"{format_moment(release.at)} by {release.released_by}: {release.reason}",
    }


def _brand(data: StockData, item_id: str) -> str:
    item = data.items.get(item_id)
    return item.brand if item else item_id


_NOT_LEGAL_ADVICE = "Batchward's reading of the price rules, not legal advice"


def price_guard_summary(limit: int = 10) -> dict:
    """Check prices today against the ceiling prices on record: batches on hand that must not
    be billed because their printed MRP is above the ceiling in force, scheduled items with no
    ceiling on record to check by hand, and the overcharge exposure from past sales above the
    allowed price, with 15% simple interest a year from each sale. Use this for any question
    about ceiling prices, overcharging or price compliance, and for the morning brief.
    `limit` is how many batches to list in each part (at most 50)."""
    data = current()
    ceilings = _ceiling_table()
    if isinstance(ceilings, dict):
        return ceilings
    on_hand = _units_on_hand(data)
    checks = [
        check_batch_price(
            data.items[key.item_id], data.batches[key], on=data.today, ceilings=ceilings
        )
        for key in sorted(on_hand)
        if key in data.batches and key.item_id in data.items
    ]
    blocked = sorted(
        (c for c in checks if c.verdict is Verdict.BLOCK),
        key=lambda c: (-(c.excess_per_unit * on_hand[c.batch]), c.batch),
    )
    to_check = sorted(
        {c.batch.item_id for c in checks if c.verdict is Verdict.WARN},
        key=lambda item_id: (_brand(data, item_id), item_id),
    )
    exposure = overcharge_exposure(
        data.ledger, data.batches, data.items, ceilings, as_of=data.today
    )
    sources: defaultdict = defaultdict(set)
    for overcharge in exposure.overcharges:
        sources[overcharge.sale.batch].add((str(overcharge.cause), overcharge.source))
    return {
        "as_of": data.today.isoformat(),
        "ceiling_prices_on_record": len(ceilings),
        "blocked_batches_on_hand": len(blocked),
        "blocked": [_price_check(c, data, on_hand[c.batch]) for c in blocked[: _limit(limit)]],
        "items_with_no_ceiling_on_record": len(to_check),
        "check_by_hand": [
            {"item_id": item_id, "brand": _brand(data, item_id)}
            for item_id in to_check[: _limit(limit)]
        ],
        "exposure": {
            "units_sold_above_allowed_price": sum(o.units for o in exposure.overcharges),
            "bill_lines": len(exposure.overcharges),
            "overcharge": _paise(exposure.amount),
            "interest": _paise(exposure.interest),
            "total": _paise(exposure.total),
            "by_cause": [
                {
                    "cause": str(cause),
                    "units": sum(o.units for o in lines),
                    "overcharge": _paise(sum((o.amount for o in lines), Decimal(0))),
                    "interest": _paise(sum((o.interest for o in lines), Decimal(0))),
                }
                for cause in Cause
                if (lines := [o for o in exposure.overcharges if o.cause is cause])
            ],
            "largest_batches": [
                {
                    "brand": _brand(data, key.item_id),
                    "batch_no": key.batch_no,
                    "units": units,
                    "overcharge": _paise(amount),
                    "interest": _paise(interest),
                    "causes": sorted({cause for cause, _ in sources[key]}),
                    "allowed_price_set_by": sorted({source for _, source in sources[key]}),
                }
                for key, (units, amount, interest) in list(exposure.by_batch().items())[
                    : _limit(limit)
                ]
            ],
        },
        "note": _NOT_LEGAL_ADVICE,
    }


def check_item_prices(item_id: str) -> dict:
    """Check whether each batch of one item on hand may be billed today: the verdict (allowed,
    warn or block), the printed MRP, the highest MRP the ceiling in force allows including GST,
    and the notification it comes from. Also shows the ceiling in force for the item and any
    batch whose MRP rose more than 10% within a year. Needs an item_id from find_items."""
    data = current()
    item = data.items.get(item_id)
    if item is None:
        return {"error": f"no item with id {item_id!r}; use find_items to look it up"}
    ceilings = _ceiling_table()
    if isinstance(ceilings, dict):
        return ceilings
    on_hand = _units_on_hand(data)
    keys = sorted(key for key in on_hand if key.item_id == item_id and key in data.batches)
    ceiling = ceilings.in_force(item, data.today)
    item_batches = [batch for key, batch in data.batches.items() if key.item_id == item_id]
    return {
        "as_of": data.today.isoformat(),
        "item_id": item_id,
        "brand": item.brand,
        "price_controlled": item.dpco_scheduled,
        "ceiling_prices_on_record": len(ceilings),
        "ceiling_in_force": None
        if ceiling is None
        else {
            "ceiling_excluding_gst": _paise(ceiling.ceiling),
            "highest_mrp_allowed": _paise(ceiling.max_retail_price(item.gst_rate)),
            "in_force_since": ceiling.effective_from.isoformat(),
            "notification": ceiling.reference,
        },
        "batches_on_hand": [
            _price_check(
                check_batch_price(item, data.batches[key], on=data.today, ceilings=ceilings),
                data,
                on_hand[key],
            )
            for key in keys
        ],
        "price_rises_above_ten_percent": [
            {
                "batch_no": rise.batch.batch_no,
                "mrp": _paise(rise.mrp),
                "earlier_batch_no": rise.reference_batch.batch_no,
                "earlier_mrp": _paise(rise.reference_mrp),
                "highest_mrp_allowed": _paise(rise.allowed_mrp),
            }
            for rise in price_rises(item_batches, data.items)
        ],
        "note": _NOT_LEGAL_ADVICE,
    }


def claims_summary(limit: int = 10) -> dict:
    """Expiry claims: stock that will not sell before expiry and can be claimed from its company
    now, the part whose claim window closes within 15 days, what was written off in the last 90
    days while it could still have been claimed, and claims already made that the company has
    not yet fully credited, with their age. Claim values are at cost times the share each
    company credits, before GST. Use this for any question about claims, expiry returns or
    money stuck with companies, and for the morning brief. `limit` is how many batches and
    claims to list (at most 50)."""
    data = current()
    path = records_path()
    if path is None or not path.is_file():
        return {"error": "no records database is set, so no return terms are on record"}
    try:
        with RecordStore(path, create=False) as store:
            terms = store.terms_table()
            claims = store.claims()
            settlements = store.settlements()
    except (RecordsError, sqlite3.Error, ValueError) as error:
        return {"error": f"the claims on record cannot be read: {error}"}
    if not len(terms):
        return {"error": "no return terms are on record; import them with `claims terms import`"}
    windows = windows_for(data, terms, claims)
    claimable = [w for w in windows if w.state in (WindowState.OPEN, WindowState.CLOSING)]
    closing = [w for w in claimable if w.state is WindowState.CLOSING]
    lost = lost_claims(
        data.ledger,
        batch_costs(data.ledger),
        terms,
        since=ist_datetime(data.today - timedelta(days=90), time(0, 0)),
        until=ist_datetime(data.today, time(0, 0)),
    )
    credited: defaultdict = defaultdict(list)
    for settlement in settlements:
        credited[settlement.claim].append(settlement)
    open_claims = []
    for claim in claims:
        paid, owed = settled(claim.total, credited[claim.number])
        if owed:
            open_claims.append((claim, paid, owed))
    open_claims.sort(key=lambda row: row[0].made_on)
    count = _limit(limit)

    def total(windows: Iterable) -> dict:
        return _paise(sum((w.claim_value or Decimal(0) for w in windows), Decimal(0)))

    return {
        "as_of": data.today.isoformat(),
        "claim_now": {"batches": len(claimable), "value": total(claimable)},
        "closing_within_days": CLOSING_DAYS,
        "closing_soon": {"batches": len(closing), "value": total(closing)},
        "written_off_while_claimable_last_90_days": {
            "write_offs": len(lost),
            "value": _paise(sum((c.value or Decimal(0) for c in lost), Decimal(0))),
        },
        "companies_without_terms": sorted(
            {w.company_id for w in windows if w.state is WindowState.NO_TERMS}
        ),
        "batches": [
            {
                "company": _party_name(data, w.company_id),
                "brand": data.items[w.batch.item_id].brand,
                "batch_no": w.batch.batch_no,
                "expiry": format_expiry(w.batch.expiry),
                "units": w.units_to_claim,
                "claim_value": _paise(w.claim_value or Decimal(0)),
                "window_closes": w.closes.isoformat() if w.closes else None,
                "state": str(w.state),
            }
            for w in claimable[:count]
        ],
        "open_claims": {
            "claims": len(open_claims),
            "owed": _paise(sum((owed for _, _, owed in open_claims), Decimal(0))),
            "oldest_days": (data.today - open_claims[0][0].made_on).days if open_claims else None,
            "listed": [
                {
                    "claim": claim.number,
                    "company": _party_name(data, claim.company_id),
                    "made_on": claim.made_on.isoformat(),
                    "age_days": (data.today - claim.made_on).days,
                    "claimed": _paise(claim.total),
                    "owed": _paise(owed),
                }
                for claim, _, owed in open_claims[:count]
            ],
        },
        "note": "Claims are drafted and sent only with a person's approval.",
    }


def order_suggestions(company_id: str = "", limit: int = 10) -> dict:
    """What to order to cover the forecast: for each item that needs ordering, its forecast
    sales per day, the units usable (sellable, not held, and expected to sell before expiry),
    units still due on orders placed, the quantity to order and its value at the last purchase
    rate, with totals by company. Orders cover 4 days' lead time and 21 days of demand. Pass a
    company_id (like "C06") for one company, or leave it empty for all. Also lists orders more
    than 30 days old with units never delivered. `limit` is how many items to list (at most 50).
    Orders are placed only with a person's approval."""
    data = current()
    path = records_path()
    placed: list[OpenOrder] = []
    held: set = set()
    if path is not None and path.is_file():
        try:
            with RecordStore(path, create=False) as store:
                placed = [OpenOrder(o, store.received_against(o.number)) for o in store.orders()]
                log = store.hold_log()
            held = {hold.batch for hold in log if log.release_of(hold.id) is None}
        except (RecordsError, sqlite3.Error, ValueError) as error:
            return {"error": f"the orders on record cannot be read: {error}"}
    due, overdue = still_due(placed, on=data.today)
    suggestions = [
        s
        for s in suggest(
            data.ledger,
            data.items,
            data.locations,
            daily_rates(data.ledger, on=data.today),
            on=data.today,
            due=due,
            held=held,
        )
        if s.quantity > 0 and (not company_id or s.item.company_id == company_id.strip().upper())
    ]
    by_company: defaultdict = defaultdict(lambda: [0, Decimal(0)])
    for s in suggestions:
        by_company[s.item.company_id][0] += 1
        by_company[s.item.company_id][1] += s.value or Decimal(0)
    return {
        "as_of": data.today.isoformat(),
        "lead_days": LEAD_DAYS,
        "cover_days": COVER_DAYS,
        "items_to_order": len(suggestions),
        "total_value": _paise(sum((s.value or Decimal(0) for s in suggestions), Decimal(0))),
        "companies": [
            {"company": _party_name(data, cid), "company_id": cid, "items": n, "value": _paise(v)}
            for cid, (n, v) in sorted(by_company.items(), key=lambda entry: -entry[1][1])
        ][: _limit(limit)],
        "items": [
            {
                "brand": s.item.brand,
                "item_id": s.item.id,
                "company_id": s.item.company_id,
                "forecast_per_day": round(s.daily_rate, 2),
                "usable_units": s.usable,
                "due_on_orders": s.due,
                "order_units": s.quantity,
                "value": _paise(s.value or Decimal(0)),
            }
            for s in suggestions[: _limit(limit)]
        ],
        "overdue_orders": [
            {
                "order": o.order.number,
                "company": _party_name(data, o.order.company_id),
                "age_days": o.age(data.today),
                "units_due": sum(o.due().values()),
            }
            for o in overdue
        ],
        "overdue_after_days": OPEN_DAYS,
        "note": "Orders are drafted with `orders draft` and placed only with a person's approval.",
    }


def _ceiling_table() -> CeilingTable | dict:
    """The ceiling prices on record, which may be none.

    With none, every scheduled item is still warned about and price rises of
    non-scheduled items are still found, so an empty table is checked, not refused.
    Only records that exist but cannot be read are an error.
    """
    path = records_path()
    if path is None or not path.is_file():
        return CeilingTable()
    try:
        with RecordStore(path, create=False) as store:
            return store.ceiling_table()
    except (RecordsError, sqlite3.Error, ValueError) as error:
        return {"error": f"the ceiling prices on record cannot be read: {error}"}


def _held_batches() -> set:
    """Batches under a hold not yet lifted, from the records if there are any."""
    path = records_path()
    if path is None or not path.is_file():
        return set()
    try:
        with RecordStore(path, create=False) as store:
            log = store.hold_log()
    except (RecordsError, sqlite3.Error):
        return set()
    return {hold.batch for hold in log if log.release_of(hold.id) is None}


def _units_on_hand(data: StockData) -> dict:
    units: defaultdict = defaultdict(int)
    for (key, _location), qty in data.ledger.balances().items():
        units[key] += qty
    return {key: qty for key, qty in units.items() if qty > 0}


def _price_check(check: PriceCheck, data: StockData, units: int) -> dict:
    return {
        "brand": _brand(data, check.batch.item_id),
        "batch_no": check.batch.batch_no,
        "expiry": format_expiry(check.batch.expiry),
        "units_on_hand": units,
        "verdict": str(check.verdict),
        "printed_mrp": _paise(data.batches[check.batch].mrp),
        "highest_mrp_allowed": None
        if check.max_retail_price is None
        else _paise(check.max_retail_price),
        "notification": None if check.ceiling is None else check.ceiling.reference,
        "reason": check.reason,
    }


def rule65_records_check(limit: int = 10) -> dict:
    """Check the records a Drugs Inspector examines under Rule 65 for the last three years:
    every sale memo carries the buyer's name, address and sale licence number, and the drug,
    quantity, batch number and manufacturer; every purchase record carries the supplier's
    name, address and licence number; and bills are numbered in date order. Returns the bills
    checked, the gaps by kind with examples, and the date from which memos must be kept.
    Use this for any question about inspection readiness, registers or memo particulars.
    `limit` is how many example gaps to list (at most 50)."""
    data = current()
    since = keep_from(data.today)
    report = rule65_check(data.ledger, data.parties, data.items, since=since, as_of=data.today)
    return {
        "period_from": since.isoformat(),
        "as_of": data.today.isoformat(),
        "sale_memos": report.sale_memos,
        "sale_lines": report.sale_lines,
        "purchase_bills": report.purchase_bills,
        "purchase_lines": report.purchase_lines,
        "complete": report.complete,
        "gaps_by_kind": [
            {"gap": str(gap), "bills": bills} for gap, bills in report.counts().items()
        ],
        "examples": [
            {
                "gap": str(finding.gap),
                "record": str(finding.record),
                "bill": finding.document_ref,
                "date": finding.day.isoformat(),
                "party": None if finding.party_id is None else _party_name(data, finding.party_id),
            }
            for finding in report.findings[: _limit(limit)]
        ],
        "memos_must_be_kept_from": report.keep_from.isoformat(),
        "memos_older_than_three_years": report.memos_past_retention,
        "not_checked": "the competent person's signature on each memo, which is on paper",
        "note": (
            "the Schedule H1 register of prescriber and patient is for retail sales, "
            "not a stockist's wholesale supplies"
        ),
    }


def _party_name(data: StockData, party_id: str) -> str:
    party = data.parties.get(party_id)
    return party.name if party else party_id


def _paise(amount: Decimal) -> dict[str, str | float]:
    return {"formatted": format_inr(amount, paise=True), "rupees": float(amount)}


ANALYST_TOOLS = (
    stock_health_summary,
    list_dead_stock,
    list_expiry_risks,
    find_items,
    item_stock,
    trace_batch_number,
    recall_status,
    price_guard_summary,
    check_item_prices,
    rule65_records_check,
    claims_summary,
)
FORECASTER_TOOLS = (find_items, forecast_item, item_stock, order_suggestions)
REPORTER_TOOLS = (
    stock_health_summary,
    list_dead_stock,
    list_expiry_risks,
    price_guard_summary,
    claims_summary,
    order_suggestions,
)
