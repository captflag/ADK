"""The morning brief: what an owner should act on today, worked out in Python (ADR 0021).

The brief gathers the analyses Batchward already makes into a few lines, each
about one thing an owner can act on and each carrying a rupee figure. Which
comes first is a rule, not a judgment (ADR 0003): what the law requires, then
money with a date on it, then today's buying, then money owed, at risk, idle,
and past exposure:

1. recalls with units still in the market, or a batch named but not blocked;
2. batches on hand that must not be billed above their ceiling price;
3. claim windows closing within 15 days;
4. what to order today, and orders never delivered;
5. credit companies still owe on claims made;
6. stock that will expire before it sells;
7. dead stock;
8. past sales above the allowed price, with interest.

A topic with nothing to report is left out. The first five that remain are the
brief's lines, and any others are named with their figures in one line after
them. Requests waiting for approval follow, oldest first, so the brief ends with
what needs a yes or a no today.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import StrEnum

from batchward.agents.data import StockData
from batchward.analysis.ageing import dead_stock
from batchward.analysis.costs import batch_costs
from batchward.analysis.expiry import expiry_exposure
from batchward.analysis.routing import daily_rates
from batchward.buying.order import OPEN_DAYS, OpenOrder, still_due
from batchward.buying.planning import open_orders
from batchward.buying.suggest import Policy, last_rates, suggest
from batchward.claims.claim import settled
from batchward.claims.submission import windows_for
from batchward.claims.windows import CLOSING_DAYS, ClaimWindow, WindowState
from batchward.compliance.prices import (
    CeilingTable,
    Verdict,
    check_batch_price,
    overcharge_exposure,
)
from batchward.compliance.recall_report import DeadlineState
from batchward.core.approvals import RequestState
from batchward.core.clock import end_of_day, ist_date, ist_datetime
from batchward.core.models import BatchKey
from batchward.records import recalls
from batchward.records.store import RecordStore
from batchward.reporting.inr import format_inr

MOST_LINES = 5
"""Lines the brief gives in full; other topics are named with their figures."""
MOST_WAITING = 10
"""Requests waiting for approval the brief lists."""
EXPIRY_WITHIN_DAYS = 180
DEAD_AFTER_DAYS = 120


class Topic(StrEnum):
    """What a line is about. They are listed most urgent first, and the brief keeps this order."""

    RECALL = "recall"
    PRICE = "do not bill"
    CLAIMS = "claims closing"
    ORDERS = "to order"
    CREDIT = "credit owed"
    EXPIRY = "expiring"
    DEAD_STOCK = "dead stock"
    OVERCHARGE = "overcharged"

    @property
    def label(self) -> str:
        return self.value[:1].upper() + self.value[1:]


@dataclass(frozen=True, slots=True)
class Line:
    topic: Topic
    rupees: Decimal
    """The line's figure: at cost, as credit, or at purchase rates, as its text says."""
    text: str


@dataclass(frozen=True, slots=True)
class Waiting:
    """A request waiting for approval."""

    number: str
    kind: str
    summary: str
    days: int
    """Whole days it has waited."""

    def text(self) -> str:
        waited = "today" if self.days <= 0 else f"{self.days} {_plural(self.days, 'day')}"
        return f"{self.number} ({self.kind}, {waited}): {self.summary}"


@dataclass(frozen=True, slots=True)
class Brief:
    on: date
    lines: tuple[Line, ...]
    """Every topic with something to report, most urgent first."""
    waiting: tuple[Waiting, ...]
    """Requests waiting for approval, oldest first."""
    not_checked: tuple[str, ...] = ()
    """What could not be looked at, and why."""

    @property
    def shown(self) -> tuple[Line, ...]:
        return self.lines[:MOST_LINES]

    @property
    def also(self) -> tuple[Line, ...]:
        return self.lines[MOST_LINES:]

    def text(self) -> str:
        """The brief as one message: numbered lines, then what waits for approval."""
        parts = [f"Batchward brief for {self.on:%a %d/%m/%Y}"]
        if not self.lines:
            parts.append("Nothing needs acting on today.")
        parts += [f"{number}. {line.text}" for number, line in enumerate(self.shown, start=1)]
        if self.also:
            also = (f"{line.topic} {format_inr(line.rupees)}" for line in self.also)
            parts.append("Also: " + "; ".join(also))
        if self.waiting:
            parts.append("Waiting for approval:")
            parts += [f"- {request.text()}" for request in self.waiting[:MOST_WAITING]]
            if len(self.waiting) > MOST_WAITING:
                parts.append(f"- and {len(self.waiting) - MOST_WAITING} more")
            first = self.waiting[0].number
            parts.append(f'Reply "approve {first}", or "reject {first}" and the reason.')
        if self.not_checked:
            parts.append("Not checked: " + "; ".join(self.not_checked) + ".")
        return "\n".join(parts)

    def headline(self) -> str:
        """The brief in one line with no line breaks, as a WhatsApp template can carry it."""
        parts = [f"{line.topic.label} {format_inr(line.rupees)}" for line in self.shown]
        if not parts:
            parts.append("Nothing needs acting on today")
        if self.waiting:
            parts.append(f"{len(self.waiting)} waiting for approval")
        return " · ".join(parts)


def morning_brief(
    stock: StockData,
    store: RecordStore | None,
    *,
    now: datetime | None = None,
    policy: Policy | None = None,
) -> Brief:
    """The brief for ``stock.today``, from Marg's stock and Batchward's records.

    Without records, what only the records know (recalls, return terms, orders
    placed, requests waiting) is named as not checked, and the rest is still worked out.
    ``now`` dates how long each request has waited.
    """
    now = now or datetime.now(UTC)
    today = stock.today
    costs = batch_costs(stock.ledger, as_of=end_of_day(today))
    rates = daily_rates(stock.ledger, on=today)
    on_hand = _on_hand(stock)
    not_checked: list[str] = []
    lines: list[Line | None] = []

    if store is None:
        not_checked.append(
            "recalls, claims, orders placed and approvals, as no records database was given"
        )
        ceilings, windows, orders, held, claims, credited = CeilingTable(), None, [], set(), [], {}
    else:
        ceilings = store.ceiling_table()
        log = store.hold_log()
        held = {hold.batch for hold in log if log.release_of(hold.id) is None}
        orders = open_orders(store)
        claims = store.claims()
        credited = defaultdict(list)
        for settlement in store.settlements():
            credited[settlement.claim].append(settlement)
        terms = store.terms_table()
        windows = windows_for(stock, terms, claims) if len(terms) else None
        if windows is None:
            not_checked.append("claim windows, as no return terms are on record")
        lines.append(_recalls(stock, store, costs, on_hand))

    exposure = overcharge_exposure(stock.ledger, stock.batches, stock.items, ceilings, as_of=today)
    lines += [
        _prices(stock, ceilings, costs, on_hand),
        _claims_closing(stock, windows),
        _orders(stock, rates, orders, held, policy),
        _credit_owed(stock, claims, credited),
        _expiry(stock, costs, rates, windows),
        _dead_stock(stock, costs),
        _overcharges(exposure.amount, exposure.interest, len(exposure.overcharges)),
    ]
    waiting = () if store is None else _waiting(store, now)
    return Brief(
        on=today,
        lines=tuple(sorted((line for line in lines if line is not None), key=_urgency)),
        waiting=waiting,
        not_checked=tuple(not_checked),
    )


_TOPICS = list(Topic)


def _urgency(line: Line) -> int:
    return _TOPICS.index(line.topic)


def _recalls(
    stock: StockData, store: RecordStore, costs: Mapping[BatchKey, Decimal], on_hand: dict
) -> Line | None:
    start_of_today = ist_datetime(stock.today, time(0, 0))
    open_recalls = []
    for notice in store.notices():
        status = recalls.recall_status(
            store,
            notice.reference,
            ledger=stock.ledger,
            items=stock.items,
            parties=stock.parties,
            as_of=max(start_of_today, notice.received_at),
            batches=stock.batches,
        )
        reports = [report for report in status.reports if report.outstanding]
        unblocked = [key for key in status.unblocked if on_hand.get(key, 0) > 0]
        if reports or unblocked:
            open_recalls.append((notice, reports, unblocked))
    if not open_recalls:
        return None
    units = sum(report.outstanding for _, reports, _ in open_recalls for report in reports)
    chemists = {
        chemist.party_id
        for _, reports, _ in open_recalls
        for report in reports
        for chemist in report.chemists
        if chemist.outstanding
    }
    unblocked = [key for _, _, keys in open_recalls for key in keys]
    rupees = sum(
        (
            costs.get(report.batch, Decimal(0)) * report.outstanding
            for _, reports, _ in open_recalls
            for report in reports
        ),
        Decimal(0),
    ) + sum((costs.get(key, Decimal(0)) * on_hand[key] for key in unblocked), Decimal(0))
    sold_after = sum(
        report.sold_after_notice for _, reports, _ in open_recalls for report in reports
    )

    single = len(open_recalls) == 1
    notice, reports, _ = open_recalls[0]
    text = f"Recall {notice.reference}: " if single else f"{len(open_recalls)} recalls are open: "
    if units:
        batches = ", ".join(sorted(report.batch.batch_no for report in reports))
        text += (
            f"{units} {_plural(units, 'unit')}{f' of batch {batches}' if single else ''} still "
            f"with {len(chemists)} {_plural(len(chemists), 'chemist')}, "
            f"{format_inr(rupees)} at cost"
        )
    else:
        text += f"{format_inr(rupees)} at cost of stock named in a notice is not blocked"
    completion = min(
        (report.completion for _, reports, _ in open_recalls for report in reports),
        key=lambda deadline: deadline.due,
        default=None,
    )
    if completion is not None:
        tense = "was" if completion.state is DeadlineState.OVERDUE else "is"
        what = "completing it" if single else "the first to complete"
        text += f"; {what} {tense} due by {ist_date(completion.due):%d/%m/%Y}"
    text += "."
    if sold_after:
        sold = _plural(sold_after, "unit was", "units were")
        text += f" {sold_after} {sold} sold after the notice."
    if unblocked:
        names = ", ".join(sorted({key.batch_no for key in unblocked}))
        text += f" Receive the notice again to block batch {names}, on hand but not blocked."
    return Line(Topic.RECALL, rupees, text)


def _prices(
    stock: StockData, ceilings: CeilingTable, costs: Mapping[BatchKey, Decimal], on_hand: dict
) -> Line | None:
    blocked = []
    for key, units in sorted(on_hand.items()):
        if key not in stock.batches or key.item_id not in stock.items:
            continue
        check = check_batch_price(
            stock.items[key.item_id], stock.batches[key], on=stock.today, ceilings=ceilings
        )
        if check.verdict is Verdict.BLOCK:
            blocked.append((costs.get(key, Decimal(0)) * units, key, units))
    if not blocked:
        return None
    rupees = sum((value for value, _, _ in blocked), Decimal(0))
    _, key, units = max(blocked, key=lambda entry: (entry[0], entry[2]))
    count = len(blocked)
    text = (
        f"Do not bill {count} {_plural(count, 'batch', 'batches')} on hand priced above their "
        f"ceiling, {format_inr(rupees)} at cost; the largest is "
        f"{stock.items[key.item_id].brand} batch {key.batch_no}, {units} "
        f"{_plural(units, 'unit')}."
    )
    return Line(Topic.PRICE, rupees, text)


def _claims_closing(stock: StockData, windows: list[ClaimWindow] | None) -> Line | None:
    closing = [w for w in windows or () if w.state is WindowState.CLOSING and w.closes]
    if not closing:
        return None
    rupees = sum((w.claim_value or Decimal(0) for w in closing), Decimal(0))
    first = min(closing, key=lambda w: (w.closes, -(w.claim_value or 0), w.batch))
    companies = {w.company_id for w in closing}
    count = len(closing)
    text = (
        f"Claim windows close within {CLOSING_DAYS} days on {count} "
        f"{_plural(count, 'batch', 'batches')} from {len(companies)} "
        f"{_plural(len(companies), 'company', 'companies')}, {format_inr(rupees)} of credit; "
        f"the first closes {first.closes:%d/%m/%Y}, for {_party(stock, first.company_id)}."
    )
    return Line(Topic.CLAIMS, rupees, text)


def _orders(
    stock: StockData,
    rates: Mapping[str, float],
    orders: list[OpenOrder],
    held: set,
    policy: Policy | None,
) -> Line | None:
    due, overdue = still_due(orders, on=stock.today)
    wanted = [
        s
        for s in suggest(
            stock.ledger,
            stock.items,
            stock.locations,
            rates,
            on=stock.today,
            due=due,
            held=held,
            policy=policy,
        )
        if s.quantity > 0
    ]
    if not wanted and not overdue:
        return None
    parts = []
    rupees = sum((s.value or Decimal(0) for s in wanted), Decimal(0))
    if wanted:
        by_company: defaultdict[str, Decimal] = defaultdict(Decimal)
        for s in wanted:
            by_company[s.item.company_id] += s.value or Decimal(0)
        largest = min(by_company, key=lambda company_id: (-by_company[company_id], company_id))
        count = len(wanted)
        parts.append(
            f"To order today: {count} {_plural(count, 'product')} from {len(by_company)} "
            f"{_plural(len(by_company), 'company', 'companies')}, about {format_inr(rupees)} at "
            f"last purchase rates; the most from {_party(stock, largest)}, "
            f"{format_inr(by_company[largest])}."
        )
    if overdue:
        bought_at = last_rates(stock.ledger)
        units = sum(sum(o.due().values()) for o in overdue)
        value = sum(
            (
                bought_at.get(item_id, Decimal(0)) * count
                for o in overdue
                for item_id, count in o.due().items()
            ),
            Decimal(0),
        )
        if not wanted:
            rupees = value
        parts.append(
            f"{len(overdue)} {_plural(len(overdue), 'order')} over {OPEN_DAYS} days old "
            f"{_plural(len(overdue), 'waits', 'wait')} for {units} {_plural(units, 'unit')}, "
            f"{format_inr(value)}: ask {_names(stock, (o.order.company_id for o in overdue))}."
        )
    return Line(Topic.ORDERS, rupees, " ".join(parts))


def _credit_owed(stock: StockData, claims: list, credited: Mapping[str, list]) -> Line | None:
    owing = []
    for claim in claims:
        _, owed = settled(claim.total, credited.get(claim.number, ()))
        if owed:
            owing.append((claim, owed))
    if not owing:
        return None
    rupees = sum((owed for _, owed in owing), Decimal(0))
    oldest, _ = min(owing, key=lambda entry: (entry[0].made_on, entry[0].number))
    count = len(owing)
    days = (stock.today - oldest.made_on).days
    text = (
        f"Companies owe {format_inr(rupees)} of credit on {count} {_plural(count, 'claim')}; "
        f"the oldest, {oldest.number} on {_party(stock, oldest.company_id)}, is {days} "
        f"{_plural(days, 'day')} old."
    )
    return Line(Topic.CREDIT, rupees, text)


def _expiry(
    stock: StockData,
    costs: Mapping[BatchKey, Decimal],
    rates: Mapping[str, float],
    windows: list[ClaimWindow] | None,
) -> Line | None:
    risks = expiry_exposure(
        stock.ledger,
        costs,
        stock.locations,
        rates,
        on=stock.today,
        within_days=EXPIRY_WITHIN_DAYS,
    )
    if not risks:
        return None
    rupees = sum((r.value_at_risk or Decimal(0) for r in risks), Decimal(0))
    batches = len({r.batch for r in risks})
    text = (
        f"{batches} {_plural(batches, 'batch', 'batches')} will expire before "
        f"{_plural(batches, 'it sells', 'they sell')}, {format_inr(rupees)} at cost"
    )
    claimable = sum(
        (
            w.claim_value or Decimal(0)
            for w in windows or ()
            if w.state in (WindowState.OPEN, WindowState.CLOSING)
        ),
        Decimal(0),
    )
    if claimable:
        text += f"; {format_inr(claimable)} of credit can be claimed from companies now"
    return Line(Topic.EXPIRY, rupees, text + ".")


def _dead_stock(stock: StockData, costs: Mapping[BatchKey, Decimal]) -> Line | None:
    dead = dead_stock(
        stock.ledger, costs, stock.locations, on=stock.today, idle_days=DEAD_AFTER_DAYS
    )
    if not dead:
        return None
    rupees = sum((d.value for d in dead), Decimal(0))
    largest = dead[0]
    count = len(dead)
    item = stock.items.get(largest.item_id)
    text = (
        f"{count} {_plural(count, 'product has', 'products have')} not sold in "
        f"{DEAD_AFTER_DAYS} days, {format_inr(rupees)} at cost; the largest is "
        f"{item.brand if item else largest.item_id}, {format_inr(largest.value)}."
    )
    return Line(Topic.DEAD_STOCK, rupees, text)


def _overcharges(amount: Decimal, interest: Decimal, bill_lines: int) -> Line | None:
    if not amount:
        return None
    text = (
        f"Past sales above the allowed price: {format_inr(amount + interest)} with interest, "
        f"{format_inr(amount)} overcharged on {bill_lines} bill {_plural(bill_lines, 'line')} "
        f"and {format_inr(interest)} of interest at 15% a year."
    )
    return Line(Topic.OVERCHARGE, amount + interest, text)


def _waiting(store: RecordStore, now: datetime) -> tuple[Waiting, ...]:
    today = ist_date(now)
    waiting = sorted(
        (
            request
            for request in store.requests()
            if store.request_state(request) is RequestState.WAITING
        ),
        key=lambda request: (request.requested_at, request.number),
    )
    return tuple(
        Waiting(
            request.number,
            request.kind,
            request.summary,
            (today - ist_date(request.requested_at)).days,
        )
        for request in waiting
    )


def _on_hand(stock: StockData) -> dict[BatchKey, int]:
    units: defaultdict[BatchKey, int] = defaultdict(int)
    for (key, _location), count in stock.ledger.balances().items():
        units[key] += count
    return {key: count for key, count in units.items() if count > 0}


def _party(stock: StockData, party_id: str) -> str:
    party = stock.parties.get(party_id)
    return party.name if party else party_id


def _names(stock: StockData, party_ids: Iterable[str]) -> str:
    names = sorted({_party(stock, party_id) for party_id in party_ids})
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _plural(count: int, one: str, many: str | None = None) -> str:
    return one if count == 1 else (many or f"{one}s")
