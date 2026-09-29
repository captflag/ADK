"""Making an approved transfer: once, only if it is still what was approved."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from batchward.compliance.quarantine import GODOWN, Move, Transfer, transfer_posting
from batchward.compliance.quarantine_run import submit
from batchward.core.clock import IST
from batchward.core.models import BatchKey
from batchward.records.store import RecordsError, RecordStore

RECALLED = BatchKey("C01", "I001", "AZ4021", date(2027, 10, 31))
AT = datetime(2026, 2, 13, 11, 30, tzinfo=IST)
TRANSFER = Transfer(
    number="QT/260213",
    godown=GODOWN,
    on=date(2026, 2, 13),
    into=(Move(RECALLED, "GODOWN", GODOWN, 120, "H00001", "recalled"),),
)


def posting(voucher: str = "VNO,LINE\n"):
    return transfer_posting(TRANSFER, {}, voucher=voucher)


@pytest.fixture
def store(tmp_path):
    with RecordStore(tmp_path / "records.sqlite") as open_store:
        yield open_store


def test_the_voucher_is_written_and_the_transfer_recorded(store, tmp_path):
    planned = posting()
    posted = submit(
        store, TRANSFER, posting(), planned, approved_by="Divyansh", at=AT, out=tmp_path / "out"
    )
    assert [path.name for path in posted.written] == [
        "QT-260213.marg-transfer.csv",
        "QT-260213.quarantine.txt",
    ]
    assert posted.approval.approved_by == "Divyansh"
    assert store.transfers() == [("QT/260213", GODOWN, date(2026, 2, 13))]
    assert len(store.quarantine_moves()) == 1


def test_a_transfer_that_has_changed_since_it_was_approved_is_refused(store, tmp_path):
    with pytest.raises(RecordsError, match="QT/260213 has changed since"):
        submit(
            store,
            TRANSFER,
            posting("more stock has arrived"),
            posting(),
            approved_by="Divyansh",
            at=AT,
            out=tmp_path / "out",
        )
    assert store.transfers() == []
    assert not (tmp_path / "out").exists()


def test_approving_the_same_transfer_again_writes_nothing_twice(store, tmp_path):
    planned = posting()
    submit(store, TRANSFER, posting(), planned, approved_by="Divyansh", at=AT, out=tmp_path / "a")
    again = submit(
        store, TRANSFER, posting(), planned, approved_by="Asha", at=AT, out=tmp_path / "b"
    )
    assert again.written == ()
    assert again.approval.approved_by == "Divyansh"
    assert not (tmp_path / "b").exists()
    assert len(store.transfers()) == 1
