from dataclasses import replace
from datetime import timedelta

import pytest

from batchward.compliance.recall import MatchKind
from batchward.compliance.recall_report import DeadlineState
from batchward.core.holds import HoldError
from batchward.core.ledger import Ledger
from batchward.core.models import BatchStatus, MovementType
from batchward.records import recalls
from batchward.records.store import RecordsError, RecordStore
from factories import at, movement
from recall_factories import ITEMS, NOTICE, PARTIES, RECALLED

LOOK_ALIKE = replace(RECALLED, batch_no="AZ4O21")


def ledger():
    return Ledger(
        [
            movement(MovementType.PURCHASE, 100, batch=RECALLED, when=at(5)),
            movement(MovementType.SALE, -30, batch=RECALLED, when=at(10), party_id="CH1"),
            movement(MovementType.PURCHASE, 50, batch=LOOK_ALIKE, when=at(6)),
        ]
    )


@pytest.fixture
def path(tmp_path):
    return tmp_path / "records.sqlite"


def receive(store, stock, notice=NOTICE, **kwargs):
    kwargs.setdefault("at", notice.received_at)
    return recalls.receive_notice(
        store, notice, ledger=stock, items=ITEMS, parties=PARTIES, **kwargs
    )


def status(store, stock, hours=100, reference=NOTICE.reference):
    return recalls.recall_status(
        store,
        reference,
        ledger=stock,
        items=ITEMS,
        parties=PARTIES,
        as_of=NOTICE.received_at + timedelta(hours=hours),
    )


def test_receiving_a_notice_records_it_and_its_block_together(path):
    stock = ledger()
    with RecordStore(path) as store:
        recall = receive(store, stock)
    (hold,) = recall.holds
    with RecordStore(path) as store:
        assert store.notice(NOTICE.reference) == NOTICE
        log = store.hold_log()
    assert list(log) == [hold]
    assert (hold.batch, hold.status, hold.placed_by) == (RECALLED, BatchStatus.BLOCKED, "system")


def test_receiving_the_same_notice_again_changes_nothing(path):
    stock = ledger()
    with RecordStore(path) as store:
        receive(store, stock)
    with RecordStore(path) as store:
        again = receive(store, stock)
        assert again.holds == ()
        assert len(store.hold_log()) == 1


def test_a_conflicting_notice_is_refused_and_blocks_nothing(path):
    stock = ledger()
    with RecordStore(path) as store:
        receive(store, stock)
        conflicting = replace(NOTICE, batch_no="AZ4O21", manufacturer=NOTICE.manufacturer)
        with pytest.raises(RecordsError, match="different notice"):
            receive(store, stock, conflicting)
        assert len(store.hold_log()) == 1


def test_a_failure_while_blocking_leaves_no_notice_behind(path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("disk unplugged")

    stock = ledger()
    with RecordStore(path) as store:
        monkeypatch.setattr(store, "save_hold", fail)
        with pytest.raises(RuntimeError):
            receive(store, stock)
        assert store.notices() == []


def test_a_release_is_recorded_and_lifts_the_block_from_then_on(path):
    stock = ledger()
    released_at = NOTICE.received_at + timedelta(hours=5)
    with RecordStore(path) as store:
        (hold,) = receive(store, stock).holds
        recalls.release_hold(
            store, hold.id, at=released_at, reason="notice withdrawn", released_by="pharmacist"
        )
    with RecordStore(path) as store:
        log = store.hold_log()
        assert log.active(RECALLED, released_at - timedelta(minutes=1)) == [hold]
        assert log.active(RECALLED) == []
        with pytest.raises(HoldError, match="already released"):
            recalls.release_hold(store, hold.id, at=released_at, reason="x", released_by="y")


def test_status_reports_each_blocked_batch_and_what_needs_review(path):
    stock = ledger()
    with RecordStore(path) as store:
        receive(store, stock)
        result = status(store, stock)
    assert [h.batch for h in result.holds] == [RECALLED]
    (report,) = result.reports
    assert (report.batch, report.supplied, report.outstanding) == (RECALLED, 30, 30)
    assert report.stop_sale.state is DeadlineState.MET
    assert [(c.batch, c.kind) for c in result.review] == [(LOOK_ALIKE, MatchKind.LOOK_ALIKE)]


def test_status_for_an_unknown_reference_says_so(path):
    with RecordStore(path) as store, pytest.raises(LookupError, match="no recall notice"):
        status(store, ledger(), reference="RN/0000")


def test_a_block_is_dated_when_it_was_placed_not_when_the_notice_arrived(path):
    stock = ledger()
    late = NOTICE.received_at + timedelta(hours=48)
    with RecordStore(path) as store:
        (hold,) = receive(store, stock, at=late).holds
        result = status(store, stock, hours=60)
    assert hold.at == late
    assert result.reports[0].stop_sale.state is DeadlineState.MET_LATE


def test_receiving_again_leaves_a_released_block_released(path):
    stock = ledger()
    with RecordStore(path) as store:
        (hold,) = receive(store, stock).holds
        recalls.release_hold(
            store,
            hold.id,
            at=NOTICE.received_at + timedelta(hours=1),
            reason="wrong batch",
            released_by="owner",
        )
        again = receive(store, stock, at=NOTICE.received_at + timedelta(hours=2))
        log = store.hold_log()
    assert again.holds == ()
    assert log.active(RECALLED) == []


def test_status_names_a_batch_the_notice_matches_but_never_blocked(path):
    """Stock of the recalled batch bought after the notice arrived was never blocked."""
    with RecordStore(path) as store:
        assert receive(store, Ledger()).holds == ()
        result = status(store, ledger())
    assert result.reports == ()
    assert result.unblocked == (RECALLED,)
