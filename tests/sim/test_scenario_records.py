"""Every scripted scenario leaves the simulated books as complete as Rule 65 requires."""

from datetime import date

from batchward.compliance.registrar import rule65_check
from batchward.sim.business import SimConfig, simulate
from batchward.sim.price_scenario import seed_price_control
from batchward.sim.scenarios import (
    recall_notice,
    seed_recall,
    seed_recall_look_alikes,
    seed_recall_returns,
)


def test_all_scenarios_together_leave_complete_rule_65_records():
    business = simulate(SimConfig(start=date(2025, 1, 1), days=60, n_chemists=60))
    recall = seed_recall(business)
    seed_recall_look_alikes(business, recall)
    seed_recall_returns(
        business, recall, notice_received=recall_notice(business, recall).received_at
    )
    seed_price_control(business)
    parties = {p.id: p for p in (*business.catalogue.companies, *business.chemists)}
    items = {item.id: item for item in business.catalogue.items}
    report = rule65_check(
        business.ledger, parties, items, since=date(2024, 1, 1), as_of=date(2026, 12, 31)
    )
    assert report.counts() == {}
    assert report.purchase_bills > 0 and report.sale_memos > 0
