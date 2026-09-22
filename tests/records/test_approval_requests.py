import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from batchward.core.approvals import Approval, Decision, RequestState, digest
from batchward.records.store import MIGRATIONS, RecordsError, RecordStore

AT = datetime(2026, 1, 30, 10, tzinfo=UTC)
BILL = "purchase:C01:ARA/25-26/00001"


def ask(store, session="s1", content="voucher v1", approval_id=BILL):
    return store.request_approval(
        approval_id=approval_id,
        kind="purchase voucher",
        digest=digest(content),
        summary="200 units",
        at=AT,
        session_id=session,
    )


def approve(store, content="voucher v1", approval_id=BILL):
    store.save_approval(
        Approval(approval_id, "purchase voucher", "Ravi", AT, digest(content), "200 units")
    )


@pytest.fixture
def store(tmp_path):
    with RecordStore(tmp_path / "records.sqlite") as store:
        yield store


def test_requests_are_numbered_in_order_and_a_run_asking_again_gets_its_own_back(store):
    first = ask(store)
    assert first.number == "A-0001"
    assert ask(store) == first
    assert ask(store, session="s2", approval_id="purchase:C01:2").number == "A-0002"
    with pytest.raises(RecordsError, match="something else"):
        ask(store, content="voucher v2")
    assert store.request("a-0001") == first
    assert store.request_for_session("s2").number == "A-0002"
    assert [request.number for request in store.requests()] == ["A-0001", "A-0002"]


def test_a_request_waits_until_decided_and_a_decision_is_given_once(store):
    request = ask(store)
    assert store.request_state(request) is RequestState.WAITING
    no = Decision("A-0001", False, "Asha", AT, note="count it again")
    assert store.save_decision(no) is True
    assert store.save_decision(no) is False
    with pytest.raises(RecordsError, match="already rejected by Asha"):
        store.save_decision(Decision("A-0001", True, "Ravi", AT))
    assert store.decision("A-0001") == no
    assert store.request_state(request) is RequestState.REJECTED


def test_an_approved_request_is_carried_out_only_if_its_digest_was_approved(store):
    request = ask(store)
    store.save_decision(Decision("A-0001", True, "Ravi", AT))
    assert store.request_state(request) is RequestState.NOT_CARRIED_OUT
    approve(store)
    assert store.request_state(request) is RequestState.APPROVED


def test_a_later_request_replaces_one_waiting_and_an_approval_elsewhere_overtakes_it(store):
    first = ask(store)
    second = ask(store, session="s2", content="voucher v2")
    assert store.request_state(first) is RequestState.REPLACED
    assert store.request_state(second) is RequestState.WAITING
    approve(store, content="voucher v3")
    assert store.request_state(second) is RequestState.OVERTAKEN


def test_a_decision_needs_its_request_and_nothing_is_ever_changed(tmp_path):
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        with pytest.raises(RecordsError, match="a decision on A-0404"):
            store.save_decision(Decision("A-0404", True, "Ravi", AT))
        ask(store)
        store.save_decision(Decision("A-0001", True, "Ravi", AT))
    with closing(sqlite3.connect(path)) as connection:
        for statement in (
            "UPDATE approval_requests SET summary = 'x'",
            "DELETE FROM approval_requests",
            "UPDATE decisions SET approved = 0",
            "DELETE FROM decisions",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="only ever added to"):
                connection.execute(statement)


def test_decisions_and_requests_need_zones_and_people():
    with pytest.raises(ValueError, match="timezone-aware"):
        Decision("A-0001", True, "Ravi", datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="the person"):
        Decision("A-0001", True, " ", AT)


def test_a_database_from_the_fourth_schema_gains_requests_and_keeps_its_approvals(tmp_path):
    path = tmp_path / "records.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.executescript(
            f"BEGIN; {''.join(MIGRATIONS[:4])} PRAGMA user_version = 4; COMMIT;"
        )
    with RecordStore(path) as store:
        approve(store)
    with RecordStore(path, create=False) as store:
        request = ask(store)
        assert store.request_state(request) is RequestState.APPROVED


def test_a_message_is_recorded_once_and_a_database_from_the_sixth_schema_gains_the_table(
    tmp_path,
):
    path = tmp_path / "records.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.executescript(
            f"BEGIN; {''.join(MIGRATIONS[:6])} PRAGMA user_version = 6; COMMIT;"
        )
    with RecordStore(path) as store:
        assert store.save_message("whatsapp", "wamid.1", "919812345678", "approve A-0001", AT)
        assert not store.save_message("whatsapp", "wamid.1", "919812345678", "again", AT)
        assert store.save_message("sms", "wamid.1", "919812345678", "other channel", AT)
        with pytest.raises(ValueError, match="timezone-aware"):
            store.save_message("whatsapp", "wamid.2", "9198", "x", datetime(2026, 1, 1))
        assert [(c, body) for c, _, _, body, _ in store.messages()] == [
            ("whatsapp", "approve A-0001"),
            ("sms", "other channel"),
        ]
    with (
        closing(sqlite3.connect(path)) as connection,
        pytest.raises(sqlite3.IntegrityError, match="only ever added to"),
    ):
        connection.execute("DELETE FROM channel_messages")
