from datetime import date, datetime
from decimal import Decimal

import pytest

from batchward.core.fefo import allocate_fefo
from batchward.core.holds import HoldError, HoldLog
from batchward.core.ledger import Ledger
from batchward.core.models import Batch, BatchStatus, MovementType
from factories import at, batch_key, movement

OLD = batch_key("B-OLD", expiry=date(2026, 6, 30))
NEW = batch_key("B-NEW", expiry=date(2027, 6, 30))


def batch(key, status=BatchStatus.LIVE):
    return Batch(key=key, manufactured=date(2025, 1, 1), mrp=Decimal(50), status=status)


def block(holds, key=OLD, day=10, status=BatchStatus.BLOCKED):
    return holds.place(
        key, status, at=at(day), reason="recall notice", reference="RN/1", placed_by="system"
    )


def test_a_hold_applies_from_the_moment_it_is_placed():
    holds = HoldLog()
    hold = block(holds, day=10)
    assert holds.active(OLD, at(9)) == []
    assert holds.active(OLD, at(10)) == [hold]
    assert holds.active(OLD) == [hold]
    assert holds.active(NEW) == []


def test_a_release_ends_the_hold_but_keeps_its_history():
    holds = HoldLog()
    hold = block(holds, day=10)
    release = holds.release(hold.id, at=at(12), reason="wrong match", released_by="pharmacist")
    assert holds.active(OLD, at(11)) == [hold]
    assert holds.active(OLD, at(12)) == []
    assert holds.active(OLD) == []
    assert holds.holds_for(OLD) == [hold]
    assert holds.release_of(hold.id) == release


def test_status_is_the_most_severe_hold_in_force():
    holds = HoldLog()
    block(holds, day=5, status=BatchStatus.QUARANTINED)
    recalled = block(holds, day=8, status=BatchStatus.RECALLED)
    live = batch(OLD)
    assert holds.status(live, at(4)) is BatchStatus.LIVE
    assert holds.status(live, at(6)) is BatchStatus.QUARANTINED
    assert holds.status(live, at(9)) is BatchStatus.RECALLED
    holds.release(recalled.id, at=at(20), reason="cleared", released_by="pharmacist")
    assert holds.status(live, at(21)) is BatchStatus.QUARANTINED


def test_a_hold_never_makes_a_batch_less_restricted_than_its_own_status():
    holds = HoldLog()
    block(holds, day=5, status=BatchStatus.QUARANTINED)
    assert holds.status(batch(OLD, BatchStatus.RECALLED), at(6)) is BatchStatus.RECALLED


def test_held_batches_are_skipped_by_first_expiry_first_out_allocation():
    ledger = Ledger(
        [
            movement(MovementType.PURCHASE, 10, batch=OLD, when=at(1)),
            movement(MovementType.PURCHASE, 10, batch=NEW, when=at(1)),
        ]
    )
    batches = {OLD: batch(OLD), NEW: batch(NEW)}
    holds = HoldLog()
    block(holds, OLD, day=10)

    def pick(when: datetime):
        return allocate_fefo(
            ledger,
            holds.apply(batches, when),
            item_id=OLD.item_id,
            location_id="GODOWN",
            qty=5,
            on=when.date(),
        )

    assert pick(at(9)) == [(OLD, 5)]
    assert pick(at(10)) == [(NEW, 5)]
    assert batches[OLD].status is BatchStatus.LIVE, "applying holds must not change the records"


def test_ids_are_assigned_in_order():
    holds = HoldLog()
    assert [block(holds).id, block(holds, NEW).id] == ["H00001", "H00002"]
    assert len(holds) == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (dict(status=BatchStatus.LIVE), "not make it live"),
        (dict(at=datetime(2026, 1, 1, 10)), "timezone-aware"),
        (dict(reason=" "), "needs a reason"),
        (dict(placed_by=""), "needs a reason and who placed it"),
    ],
)
def test_refuses_holds_that_say_nothing_useful(change, message):
    fields = dict(
        status=BatchStatus.BLOCKED, at=at(1), reason="recall", reference="RN/1", placed_by="system"
    )
    with pytest.raises(HoldError, match=message):
        HoldLog().place(OLD, **(fields | change))


def test_refuses_to_release_twice_early_or_unknown_holds():
    holds = HoldLog()
    hold = block(holds, day=10)
    with pytest.raises(HoldError, match="before it was placed"):
        holds.release(hold.id, at=at(9), reason="oops", released_by="pharmacist")
    holds.release(hold.id, at=at(11), reason="cleared", released_by="pharmacist")
    with pytest.raises(HoldError, match="already released"):
        holds.release(hold.id, at=at(12), reason="again", released_by="pharmacist")
    with pytest.raises(HoldError, match="no hold"):
        holds.release("H99999", at=at(12), reason="?", released_by="pharmacist")
