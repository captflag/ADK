from datetime import date

import pytest

from batchward.compliance.recall_report import DeadlineState
from batchward.sim.business import SimConfig, simulate
from batchward.sim.recall_drill import DrillCheck, DrillResult, run_recall_drill


@pytest.fixture(scope="module")
def drill() -> DrillResult:
    business = simulate(SimConfig(start=date(2026, 1, 1), days=45, n_chemists=60))
    return run_recall_drill(business)


def test_the_drill_passes_every_check(drill):
    failed = [f"{c.name}: {c.detail}" for c in drill.checks if not c.passed]
    assert failed == []
    assert drill.passed
    assert len(drill.checks) == 8


def test_blocks_the_recalled_batch_and_nothing_else(drill):
    assert drill.recall.match.exact == (drill.scenario.batch,)
    assert [hold.batch for hold in drill.recall.holds] == [drill.scenario.batch]
    assert {c.batch for c in drill.recall.match.review} == set(drill.look_alikes)


def test_the_report_reconciles_the_returns_that_followed(drill):
    report, returns = drill.report, drill.returns
    outstanding = {c.party_id for c in report.chemists if c.outstanding}
    assert outstanding == returns.never
    assert report.stop_sale.state is DeadlineState.MET
    assert report.completion.state is DeadlineState.OVERDUE
    assert report.recovered + report.outstanding == report.supplied == 790
    assert "Needs attention" in drill.report_text


def test_a_failed_check_fails_the_drill(drill):
    failing = DrillResult(
        **{
            field: getattr(drill, field)
            for field in DrillResult.__dataclass_fields__
            if field != "checks"
        },
        checks=(*drill.checks, DrillCheck("impossible", False, "")),
    )
    assert not failing.passed
