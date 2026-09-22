from datetime import date, time

import pytest

from batchward.compliance.recall import RecallClass, batches_ever_held, match_notice
from batchward.core.clock import ist_datetime
from batchward.core.models import MovementType
from batchward.core.trace import trace_batch
from batchward.sim.business import RETURNS, SimConfig, location_for, simulate
from batchward.sim.scenarios import (
    recall_notice,
    seed_recall,
    seed_recall_look_alikes,
    seed_recall_returns,
)


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


def drill_business():
    b = business()
    scenario = seed_recall(b)
    notice = recall_notice(b, scenario)
    return b, scenario, notice


def test_the_notice_names_the_batch_the_way_a_manufacturer_writes_it():
    b, scenario, notice = drill_business()
    company = next(c for c in b.catalogue.companies if c.id == scenario.batch.company_id)
    assert notice.recall_class is RecallClass.I
    assert notice.received_at == ist_datetime(scenario.notice_date, time(9, 15))
    assert (notice.batch_no, notice.expiry) == ("AZ4021", scenario.batch.expiry)
    assert notice.manufacturer == f"M/s. {company.name} Ltd., {company.address}"
    assert notice.product == "Azithromycin Tablets IP 500 mg"


def test_the_notice_matches_the_seeded_batch_exactly():
    b, scenario, notice = drill_business()
    items = {i.id: i for i in b.catalogue.items}
    parties = {c.id: c for c in b.catalogue.companies}
    result = match_notice(notice, batches_ever_held(b.ledger), items=items, parties=parties)
    assert result.exact == (scenario.batch,)


def test_chemists_return_the_batch_promptly_late_or_never():
    b, scenario, notice = drill_business()
    returns = seed_recall_returns(b, scenario, notice_received=notice.received_at)
    assert (len(returns.late), len(returns.never)) == (4, 2)
    assert returns.prompt | returns.late | returns.never == scenario.chemists
    assert not (returns.prompt & returns.late or returns.never & (returns.prompt | returns.late))

    held = {
        r.party_id: r.units
        for r in trace_batch(b.ledger, scenario.batch, as_of=notice.received_at).recipients
    }
    credit_notes = [
        m for m in b.ledger.movements_for(scenario.batch) if m.kind is MovementType.SALE_RETURN
    ]
    assert {m.party_id for m in credit_notes} == returns.prompt | returns.late
    for m in credit_notes:
        assert m.qty == held[m.party_id]
        assert m.location_id == RETURNS.id
        hours = (m.at - notice.received_at).total_seconds() / 3600
        assert 2 <= hours <= 70 if m.party_id in returns.prompt else 80 <= hours <= 230
    remaining = trace_batch(b.ledger, scenario.batch).recipients
    assert {r.party_id for r in remaining} == returns.never


def test_returns_are_the_same_every_run():
    first, second = drill_business(), drill_business()
    a = seed_recall_returns(first[0], first[1], notice_received=first[2].received_at)
    b = seed_recall_returns(second[0], second[1], notice_received=second[2].received_at)
    assert a == b
    assert [m.at for m in first[0].ledger if m.kind is MovementType.SALE_RETURN] == [
        m.at for m in second[0].ledger if m.kind is MovementType.SALE_RETURN
    ]


def test_look_alike_batches_are_held_but_never_the_recalled_one():
    b, scenario, _ = drill_business()
    look_alikes = seed_recall_look_alikes(b, scenario)
    key = scenario.batch
    assert [(d.batch_no, d.expiry, d.company_id == key.company_id) for d in look_alikes] == [
        ("AZ4O21", key.expiry, True),
        ("AZ4021", date(2026, 10, 31), True),
        ("AZ4021", key.expiry, False),
    ]
    items = {i.id: i for i in b.catalogue.items}
    for decoy in look_alikes:
        assert decoy in b.batches
        assert b.ledger.balance(decoy, location_for(items[decoy.item_id])) == 150
