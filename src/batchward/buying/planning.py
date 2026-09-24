"""Planning orders from Marg and the records, and placing one once approved (ADR 0020).

Suggestions are worked out from the day's stock and forecast, less batches under
a hold, plus what is still due on orders placed through Batchward. An order on
one company is put up for approval (ADR 0005); on approval it is planned again
and placed only if it is still exactly what the person saw, with the approval,
the order and its files recorded together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from batchward.agents.data import StockData, load_marg
from batchward.analysis.routing import daily_rates
from batchward.buying.order import (
    KIND,
    OPEN_DAYS,
    OpenOrder,
    draft_order,
    order_posting,
    still_due,
)
from batchward.buying.suggest import Policy, Suggestion, suggest
from batchward.core.approvals import Approval, Posted, Posting, write_files
from batchward.core.orders import PurchaseOrder
from batchward.records.store import RecordsError, RecordStore


@dataclass(frozen=True, slots=True)
class Planned:
    stock: StockData
    suggestions: list[Suggestion]
    open_orders: list[OpenOrder]
    overdue: list[OpenOrder]
    order: PurchaseOrder | None
    posting: Posting | None


def open_orders(store: RecordStore) -> list[OpenOrder]:
    """Every order placed through Batchward, with what approved bills received against it."""
    return [OpenOrder(order, store.received_against(order.number)) for order in store.orders()]


def plan_for(
    marg: Path,
    records: Path,
    *,
    policy: Policy | None = None,
    company_id: str | None = None,
) -> Planned:
    """What to order today from every company, and an order on ``company_id`` if given."""
    stock = load_marg(marg)
    with RecordStore(records, create=False) as store:
        orders = open_orders(store)
        holds = store.hold_log()
        cases = store.case_table()
    held = {hold.batch for hold in holds if holds.release_of(hold.id) is None}
    due, overdue = still_due(orders, on=stock.today, open_days=OPEN_DAYS)
    suggestions = suggest(
        stock.ledger,
        stock.items,
        stock.locations,
        daily_rates(stock.ledger, on=stock.today),
        on=stock.today,
        due=due,
        held=held,
        policy=policy,
        cases=cases,
    )
    order = posting = None
    if company_id is not None:
        company = stock.parties.get(company_id)
        if company is None:
            raise ValueError(f"no company {company_id} in Marg")
        order = draft_order(suggestions, company_id=company_id, on=stock.today)
        if order is not None:
            by_item = {s.item.id: s for s in suggestions}
            posting = order_posting(order, company, by_item)
    return Planned(stock, suggestions, orders, overdue, order, posting)


def place(
    store: RecordStore,
    order: PurchaseOrder,
    current: Posting,
    planned: Posting,
    *,
    approved_by: str,
    at: datetime,
    out: Path,
) -> Posted:
    """Record the approved order and write its files, all or nothing.

    ``current`` is the order planned again just before; RecordsError if it is no
    longer what was approved. The same order approved again writes nothing.
    """
    if current.digest != planned.digest:
        raise RecordsError(
            f"order {order.number} has changed since it was put up for approval; draft it again"
        )
    approval = Approval(planned.approval_id, KIND, approved_by, at, planned.digest, planned.summary)
    with store.atomically():
        if not store.save_approval(approval):
            earlier = store.approval(approval.id)
            assert earlier is not None
            return Posted(earlier, ())
        store.save_order(order, approval.id)
        written = write_files(out, planned.files)
    return Posted(approval, written)
