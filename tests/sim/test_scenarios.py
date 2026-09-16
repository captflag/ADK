from datetime import date, time

import pytest

from batchward.core.trace import trace_batch
from batchward.sim.business import SimConfig, ist_datetime, simulate
from batchward.sim.scenarios import seed_recall


def business(n_chemists=60):
    return simulate(SimConfig(start=date(2026, 1, 1), days=1, n_chemists=n_chemists))


@pytest.fixture(scope="module")
def recalled():
    b = business()
    return b, seed_recall(b)


def test_the_recalled_batch_traces_to_exactly_the_38_chemists(recalled):
    b, scenario = recalled
    trace = trace_batch(b.ledger, scenario.batch)
    assert len(trace.recipients) == 38
    assert {r.party_id for r in trace.recipients} == scenario.chemists


def test_every_unit_of_the_recalled_batch_is_accounted_for(recalled):
    b, scenario = recalled
    trace = trace_batch(b.ledger, scenario.batch)
    assert trace.received == scenario.units_received == 1_000
    assert trace.supplied == scenario.units_supplied == 790
    assert sum(trace.on_hand.values()) == scenario.units_on_hand == 210
    assert trace.untraceable == 0


def test_all_supplies_happened_before_the_recall_notice(recalled):
    b, scenario = recalled
    trace = trace_batch(b.ledger, scenario.batch)
    assert all(r.last_supplied.date() < scenario.notice_date for r in trace.recipients)


def test_the_position_on_the_notice_date_is_the_full_story(recalled):
    b, scenario = recalled
    end_of_notice_day = ist_datetime(scenario.notice_date, time(23, 59))
    on_notice = trace_batch(b.ledger, scenario.batch, as_of=end_of_notice_day)
    assert on_notice == trace_batch(b.ledger, scenario.batch)


def test_is_a_class_one_recall_with_every_chemist_getting_at_least_five_units(recalled):
    b, scenario = recalled
    assert scenario.recall_class == "I"
    assert min(r.units for r in trace_batch(b.ledger, scenario.batch).recipients) >= 5


def test_the_same_seed_picks_the_same_chemists():
    assert seed_recall(business()).chemists == seed_recall(business()).chemists


def test_cannot_seed_the_same_batch_twice():
    b = business()
    seed_recall(b)
    with pytest.raises(ValueError, match="already in the business"):
        seed_recall(b)


def test_needs_enough_chemists():
    with pytest.raises(ValueError, match="only 20 chemists"):
        seed_recall(business(n_chemists=20))


def test_needs_a_brand_of_the_molecule():
    with pytest.raises(LookupError, match="no Unobtainium"):
        seed_recall(business(), molecule="Unobtainium")
