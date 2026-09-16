"""Phase 0 exit test: three years of trading survive a round trip through Marg.

A distributor's real history runs to years, not weeks. This check simulates
three years of trading, writes it to Marg-shaped tables, rebuilds the ledger
from them, and requires the rebuilt history to match the original exactly.
It takes minutes, so it runs weekly rather than on every push.
"""

import sqlite3
from collections import Counter
from contextlib import closing
from datetime import date, time
from decimal import Decimal

import pytest

from batchward.bridge.marg_export import export_to_marg
from batchward.bridge.marg_import import read_ledger, read_masters
from batchward.bridge.reconcile import reconcile_stock
from batchward.core.clock import ist_datetime
from batchward.core.models import MovementType
from batchward.core.trace import trace_batch
from batchward.sim.business import SimConfig, price_to_stockist, simulate
from batchward.sim.scenarios import seed_recall

pytestmark = pytest.mark.slow

START = date(2023, 9, 1)
DAYS = 1_096  # through 31 August 2026


@pytest.fixture(scope="module")
def replayed():
    business = simulate(SimConfig(start=START, days=DAYS))
    recall = seed_recall(business)
    with closing(sqlite3.connect(":memory:")) as connection:
        export_to_marg(
            connection,
            parties=[*business.catalogue.companies, *business.chemists],
            items=business.catalogue.items,
            batches=business.batches,
            ledger=business.ledger,
        )
        masters = read_masters(connection)
        rebuilt = read_ledger(connection, masters)
    return business, recall, masters, rebuilt


def test_every_movement_is_replayed(replayed):
    business, _, _, rebuilt = replayed
    assert len(rebuilt) == len(business.ledger)
    assert Counter(m.kind for m in rebuilt) == Counter(m.kind for m in business.ledger)


def test_closing_stock_matches_batch_by_batch(replayed):
    business, _, _, rebuilt = replayed
    assert rebuilt.balances() == business.ledger.balances()


@pytest.mark.parametrize("month_end", [date(2024, 3, 31), date(2025, 3, 31), date(2026, 3, 31)])
def test_stock_at_each_financial_year_end_matches(replayed, month_end):
    business, _, _, rebuilt = replayed
    as_of = ist_datetime(month_end, time(23, 59))
    assert rebuilt.balances(as_of) == business.ledger.balances(as_of)


def test_marg_stock_figures_reconcile_with_the_bills(replayed):
    _, _, masters, rebuilt = replayed
    assert reconcile_stock(masters, rebuilt) == []


def test_the_recall_traces_identically_after_three_years_of_history(replayed):
    business, recall, _, rebuilt = replayed
    trace = trace_batch(rebuilt, recall.batch)
    assert trace == trace_batch(business.ledger, recall.batch)
    assert {r.party_id for r in trace.recipients} == recall.chemists


def test_three_years_contain_the_problems_a_real_stockist_carries(replayed):
    business, _, _, _ = replayed
    kinds = Counter(m.kind for m in business.ledger)
    assert kinds[MovementType.WRITE_OFF] > 0, "no stock ever expired"
    assert kinds[MovementType.SALE_RETURN] > 0, "no chemist ever returned near-expiry stock"
    assert sum(business.unmet_demand.values()) > 0, "no order ever went short"


def test_expired_stock_stays_within_a_plausible_share_of_purchases(replayed):
    """Trade sources put expiry losses roughly between 0.05% and 1.5% of value; none is
    verified, so the band is deliberately wide. It catches a simulator that stops
    expiring anything, or one that expires absurd amounts."""
    business, _, _, _ = replayed
    items = {item.id: item for item in business.catalogue.items}

    def cost(m):
        item = items[m.batch.item_id]
        return price_to_stockist(item.mrp, item.gst_rate) * abs(m.qty)

    bought = sum(cost(m) for m in business.ledger if m.kind is MovementType.PURCHASE)
    expired = sum(cost(m) for m in business.ledger if m.kind is MovementType.WRITE_OFF)
    assert Decimal("0.001") <= expired / bought <= Decimal("0.03")
