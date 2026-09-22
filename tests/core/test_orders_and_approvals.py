import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from batchward.core.approvals import Approval, digest
from batchward.core.orders import OrderLine, PurchaseOrder
from batchward.records.store import MIGRATIONS, RecordsError, RecordStore

ORDER = PurchaseOrder(
    "PO/C01/260112", "C01", date(2026, 1, 12), (OrderLine("C01-001", 50), OrderLine("C01-002", 20))
)


def test_an_order_reads_back_from_its_json():
    assert PurchaseOrder.from_json(ORDER.to_json()) == ORDER
    assert ORDER.ordered("C01-002") == 20
    assert ORDER.ordered("C09-999") == 0


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: OrderLine("C01-001", 0), "at least one unit"),
        (lambda: PurchaseOrder(" ", "C01", date(2026, 1, 1), ()), "needs a number"),
        (
            lambda: PurchaseOrder(
                "PO/1", "C01", date(2026, 1, 1), (OrderLine("A", 1), OrderLine("A", 2))
            ),
            "lists an item twice",
        ),
        (lambda: PurchaseOrder.from_json('{"number": "PO/1"}'), "not a purchase order"),
        (lambda: PurchaseOrder.from_json("not json"), "not a purchase order"),
    ],
)
def test_an_unusable_order_is_refused(build, message):
    with pytest.raises(ValueError, match=message):
        build()


def approval(content="voucher v1", by="Ravi"):
    return Approval(
        id="purchase:C01:NIL/25-26/00001",
        kind="purchase voucher",
        approved_by=by,
        at=datetime(2026, 1, 30, 10, tzinfo=UTC),
        digest=digest(content),
        summary="200 units on 1 line",
    )


def test_an_approval_is_recorded_once_and_a_changed_version_is_refused(tmp_path):
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        assert store.save_approval(approval()) is True
        assert store.save_approval(approval(by="someone else")) is False
        with pytest.raises(RecordsError, match="already approved by Ravi"):
            store.save_approval(approval(content="voucher v2"))
    with RecordStore(path, create=False) as store:
        assert store.approvals() == [approval()]
        assert store.approval("purchase:C99:NONE") is None


def test_an_approval_needs_a_zone_a_person_and_a_digest():
    with pytest.raises(ValueError, match="timezone-aware"):
        Approval("a", "k", "Ravi", datetime(2026, 1, 1), digest("x"), "s")
    with pytest.raises(ValueError, match="by whom"):
        Approval("a", "k", " ", datetime(2026, 1, 1, tzinfo=UTC), digest("x"), "s")
    with pytest.raises(ValueError, match="SHA-256"):
        Approval("a", "k", "Ravi", datetime(2026, 1, 1, tzinfo=UTC), "abc", "s")


def test_what_a_bill_received_against_an_order_is_added_up_by_item(tmp_path):
    with RecordStore(tmp_path / "records.sqlite") as store:
        store.save_approval(approval())
        other = replace(approval(), id="purchase:C01:NIL/25-26/00002")
        store.save_approval(other)
        store.save_received(approval().id, "PO/C01/260112", {"C01-001": 30})
        store.save_received(other.id, "po/c01/ 260112", {"C01-001": 20, "C01-002": 20})
        assert store.received_against("PO/C01/260112") == {"C01-001": 50, "C01-002": 20}
        assert store.received_against("PO/C01/260112", excluding=other.id) == {"C01-001": 30}
        assert store.received_against("PO/C09/000000") == {}


def test_receipts_against_orders_need_their_approval_and_are_never_changed(tmp_path):
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        with pytest.raises(RecordsError, match="cannot record 5 units of C01-001"):
            store.save_received("purchase:nobody:1", "PO/1", {"C01-001": 5})
        store.save_approval(approval())
        with pytest.raises(RecordsError, match="cannot record 0 units"):
            store.save_received(approval().id, "PO/1", {"C01-001": 0})
        store.save_received(approval().id, "PO/1", {"C01-001": 5})
        with pytest.raises(RecordsError, match="cannot record"):
            store.save_received(approval().id, "PO/1", {"C01-001": 5})
    with closing(sqlite3.connect(path)) as connection:
        for statement in (
            "UPDATE received_on_orders SET units = 9",
            "DELETE FROM received_on_orders",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="only ever added to"):
                connection.execute(statement)


def test_a_database_with_approvals_from_the_third_schema_gains_the_receipts_table(tmp_path):
    path = tmp_path / "records.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.executescript(
            f"BEGIN; {''.join(MIGRATIONS[:3])} PRAGMA user_version = 3; COMMIT;"
        )
    with RecordStore(path) as store:
        store.save_approval(approval())
        store.save_received(approval().id, "PO/1", {"C01-001": 5})
    with RecordStore(path, create=False) as store:
        assert store.approvals() == [approval()]
        assert store.received_against("PO/1") == {"C01-001": 5}
