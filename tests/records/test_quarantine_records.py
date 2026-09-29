"""Keeping the transfers made, so released stock knows which godown to go back to."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pytest

from batchward.compliance.quarantine import GODOWN, Move, Transfer
from batchward.core.approvals import Approval
from batchward.core.clock import IST
from batchward.core.models import BatchKey
from batchward.records.store import RecordsError, RecordStore

RECALLED = BatchKey("C01", "I001", "AZ4021", date(2027, 10, 31))
OTHER = BatchKey("C02", "I002", "PCM9981", date(2028, 12, 31))
MADE = datetime(2026, 2, 13, 11, 0, tzinfo=IST)


def transfer(number: str = "QT/260213", *moves: Move) -> Transfer:
    into = moves or (Move(RECALLED, "MAIN", GODOWN, 120, "H00001", "recalled"),)
    return Transfer(number=number, godown=GODOWN, on=MADE.date(), into=into)


@pytest.fixture
def store(tmp_path):
    with RecordStore(tmp_path / "records.sqlite") as open_store:
        yield open_store


def approve(store: RecordStore, made: Transfer) -> str:
    approval_id = f"quarantine:{made.number}"
    store.save_approval(
        Approval(approval_id, "quarantine transfer", "Divyansh", MADE, "0" * 64, made.summary())
    )
    return approval_id


def test_a_transfer_and_its_moves_are_kept(store):
    made = transfer()
    with store.transaction():
        store.save_transfer(made, approve(store, made))
    assert store.transfers() == [("QT/260213", GODOWN, date(2026, 2, 13))]
    (kept,) = store.quarantine_moves()
    assert kept == made.into[0]


def test_both_directions_are_kept_in_the_order_they_were_made(store):
    made = Transfer(
        number="QT/260213",
        godown=GODOWN,
        on=MADE.date(),
        into=(Move(RECALLED, "MAIN", GODOWN, 120, "H00001"),),
        back=(Move(OTHER, GODOWN, "COLD", 30, "H00002"),),
    )
    with store.transaction():
        store.save_transfer(made, approve(store, made))
    assert [(move.from_location, move.to_location) for move in store.quarantine_moves()] == [
        ("MAIN", GODOWN),
        (GODOWN, "COLD"),
    ]


def test_the_same_transfer_cannot_be_recorded_twice(store):
    made = transfer()
    with store.transaction():
        store.save_transfer(made, approve(store, made))
    with pytest.raises(RecordsError, match="quarantine transfer QT/260213"), store.transaction():
        store.save_transfer(made, "quarantine:QT/260213")


def test_moves_come_back_in_the_order_the_transfers_were_made(store):
    first = transfer("QT/260213")
    second = Transfer(
        number="QT/260214",
        godown=GODOWN,
        on=date(2026, 2, 14),
        into=(Move(OTHER, "COLD", GODOWN, 40, "H00002"),),
    )
    for made in (first, second):
        with store.transaction():
            store.save_transfer(made, approve(store, made))
    assert [move.batch.batch_no for move in store.quarantine_moves()] == ["AZ4021", "PCM9981"]
    assert [number for number, _, _ in store.transfers()] == ["QT/260213", "QT/260214"]


def test_the_records_refuse_to_be_rewritten(store):
    made = transfer()
    with store.transaction():
        store.save_transfer(made, approve(store, made))
    for written in ("UPDATE quarantine_moves SET units = 1",
                    "DELETE FROM quarantine_transfers"):  # fmt: skip
        with pytest.raises(sqlite3.IntegrityError, match="only ever added to"):
            store._connection.execute(written)


def test_a_database_with_nothing_moved_yet(store):
    assert store.transfers() == [] and store.quarantine_moves() == []
