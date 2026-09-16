"""A recall must not depend on whether stock data came from Batchward or from Marg."""

from batchward.core.trace import trace_batch


def test_the_recall_trace_is_identical_after_a_round_trip_through_marg(business, rebuilt, recall):
    assert trace_batch(rebuilt, recall.batch) == trace_batch(business.ledger, recall.batch)


def test_the_round_tripped_recall_still_finds_all_38_chemists_and_210_on_hand(rebuilt, recall):
    trace = trace_batch(rebuilt, recall.batch)
    assert {r.party_id for r in trace.recipients} == recall.chemists
    assert trace.supplied == recall.units_supplied
    assert sum(trace.on_hand.values()) == recall.units_on_hand


def test_traces_of_ordinary_batches_also_survive_the_round_trip(business, rebuilt):
    sample = sorted(business.batches)[::40]
    assert len(sample) >= 20
    for key in sample:
        assert trace_batch(rebuilt, key) == trace_batch(business.ledger, key), key
