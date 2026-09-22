"""The recall drill against three years of history.

The drill must search every batch the stockist has ever held, so its speed
depends on the size of that history. This runs it against three simulated
years — the length of history a real distributor carries — and holds it to
the same two-minute limit. It runs weekly with the other slow tests.
"""

from datetime import date

import pytest

from batchward.sim.business import SimConfig, simulate
from batchward.sim.recall_drill import TIME_LIMIT, run_recall_drill

pytestmark = pytest.mark.slow


def test_the_recall_drill_passes_against_three_years_of_history():
    business = simulate(SimConfig(start=date(2023, 9, 1), days=1_096))
    history = len(business.ledger)
    result = run_recall_drill(business)

    assert history > 500_000
    failed = [f"{c.name}: {c.detail}" for c in result.checks if not c.passed]
    assert failed == []
    assert result.seconds_to_block + result.seconds_to_report < TIME_LIMIT.total_seconds()
