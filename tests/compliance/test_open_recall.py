from dataclasses import replace
from datetime import timedelta

import pytest

from batchward.compliance.recall import open_recall
from batchward.core.holds import HoldLog
from batchward.core.ledger import Ledger
from batchward.core.models import BatchStatus, MovementType
from factories import at, movement
from recall_factories import ITEMS, NOTICE, PARTIES, RECALLED

LOOK_ALIKE = replace(RECALLED, batch_no="AZ4O21")


def held_ledger():
    return Ledger(
        [
            movement(MovementType.PURCHASE, 100, batch=RECALLED, when=at(5)),
            movement(MovementType.PURCHASE, 100, batch=LOOK_ALIKE, when=at(6)),
        ]
    )


def open_(holds, ledger=None, **kwargs):
    kwargs.setdefault("at", NOTICE.received_at)
    return open_recall(
        NOTICE, ledger=ledger or held_ledger(), items=ITEMS, parties=PARTIES, holds=holds, **kwargs
    )


def test_blocks_the_exact_match_at_once_and_only_that():
    holds = HoldLog()
    recall = open_(holds)
    (hold,) = recall.holds
    assert (hold.batch, hold.status, hold.at) == (RECALLED, BatchStatus.BLOCKED, NOTICE.received_at)
    assert hold.placed_by == "system"
    assert hold.reference == NOTICE.reference
    assert "Class I" in hold.reason
    assert holds.active(LOOK_ALIKE) == []
    assert [c.batch for c in recall.match.review] == [LOOK_ALIKE]


def test_matches_batches_no_longer_in_stock():
    ledger = held_ledger()
    ledger.append(movement(MovementType.SALE, -100, batch=RECALLED, when=at(7), party_id="CH1"))
    assert open_(HoldLog(), ledger=ledger).match.exact == (RECALLED,)


def test_processing_the_same_notice_twice_places_one_hold():
    holds = HoldLog()
    open_(holds)
    again = open_(holds)
    assert again.holds == ()
    assert again.match.exact == (RECALLED,)
    assert len(holds) == 1


def test_a_block_a_person_released_stays_released_when_the_notice_is_processed_again():
    """A nightly job receiving the notice again must not silently undo a person's decision."""
    holds = HoldLog()
    (hold,) = open_(holds).holds
    holds.release(
        hold.id, at=NOTICE.received_at + timedelta(hours=1), reason="check", released_by="owner"
    )
    assert open_(holds, at=NOTICE.received_at + timedelta(hours=2)).holds == ()
    assert holds.active(RECALLED) == []


def test_can_act_after_receipt_but_not_before():
    later = NOTICE.received_at + timedelta(hours=3)
    assert open_(HoldLog(), at=later).holds[0].at == later
    with pytest.raises(ValueError, match="before its notice was received"):
        open_(HoldLog(), at=NOTICE.received_at - timedelta(minutes=1))


def test_deadlines_run_from_receipt_of_the_notice():
    recall = open_(HoldLog())
    assert recall.stop_sale_due == NOTICE.received_at + timedelta(hours=24)
    assert recall.complete_due == NOTICE.received_at + timedelta(hours=72)


def test_a_batch_on_record_with_no_movement_is_still_blocked():
    """Opening stock a billing system holds without a bill must not escape a recall."""
    recall = open_(HoldLog(), ledger=Ledger(), batches=[RECALLED])
    assert [hold.batch for hold in recall.holds] == [RECALLED]
