import asyncio
import json
from datetime import date

import pytest
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from batchward.agents import approval_flow, receiving
from batchward.agents.approval_flow import REQUEST_INPUT, USER, Answer
from batchward.cli import main
from batchward.core.approvals import RequestState
from batchward.intake.delivery import Delivery, match_delivery
from batchward.intake.posting import posting
from batchward.records.store import RecordsError, RecordStore


@pytest.fixture(scope="module")
def delivery(tmp_path_factory):
    folder = tmp_path_factory.mktemp("receiving")
    marg, records, invoices = folder / "marg.sqlite", folder / "rec.sqlite", folder / "inv"
    demo = ["demo-marg", str(marg), "--start", "2026-01-01", "--days", "30", "--chemists", "40"]
    assert main([*demo, "--invoices", str(invoices), "--records", str(records)]) == 0
    reading = sorted(path for path in invoices.glob("*.json") if ".order" not in path.name)[0]
    order_no = json.loads(reading.read_text(encoding="utf-8"))["order_no"]
    return Delivery(
        invoice=reading,
        count=reading.with_suffix(".count.csv"),
        marg=marg,
        received_on=date(2026, 1, 30),
        order=invoices / (order_no.replace("/", "-") + ".order.json"),
        records=records,
    )


@pytest.fixture
def fresh(delivery, tmp_path):
    """The delivery with records of its own."""
    records = tmp_path / "rec.sqlite"
    records.write_bytes(delivery.records.read_bytes())
    return Delivery(**{**_fields(delivery), "records": records})


def _fields(delivery):
    return {name: getattr(delivery, name) for name in delivery.__slots__}


def put_up(delivery, tmp_path):
    receipt, _ = match_delivery(delivery)
    planned = posting(receipt, received_on=delivery.received_on)
    return asyncio.run(receiving.ask_to_post(delivery, planned, out=tmp_path / "posted")), planned


def test_a_delivery_keeps_its_paths_through_a_paused_run(delivery):
    assert Delivery.from_state(json.loads(json.dumps(delivery.to_state()))) == delivery


def test_the_run_pauses_until_an_answer_arrives_from_a_later_process(fresh, tmp_path):
    request, planned = put_up(fresh, tmp_path)
    with RecordStore(fresh.records) as store:
        assert store.request_state(request) is RequestState.WAITING
    assert asyncio.run(approval_flow.planned(request, records=fresh.records)) == planned
    assert not (tmp_path / "posted").exists()

    outcome = asyncio.run(
        approval_flow.answer(request, Answer(approved=True, by=" Ravi "), records=fresh.records)
    )
    assert (outcome.outcome, outcome.by) == ("posted", "Ravi")
    assert sorted(p.name for p in (tmp_path / "posted").iterdir()) == sorted(planned.files)
    with RecordStore(fresh.records) as store:
        assert store.request_state(request) is RequestState.APPROVED
    with pytest.raises(RecordsError, match="not waiting for an answer"):
        asyncio.run(
            approval_flow.answer(request, Answer(approved=True, by="Ravi"), records=fresh.records)
        )


def test_an_answer_that_does_not_read_is_asked_for_again(fresh, tmp_path):
    request, _ = put_up(fresh, tmp_path)

    async def reply_with(response):
        service = SqliteSessionService(str(approval_flow.runs_path(fresh.records)))
        session = await service.get_session(
            app_name=receiving.FLOW.app, user_id=USER, session_id=request.session_id
        )
        waiting = approval_flow.pending(session)
        part = types.Part(
            function_response=types.FunctionResponse(
                id=waiting, name=REQUEST_INPUT, response=response
            )
        )
        await approval_flow._run(
            receiving.FLOW, service, session, types.Content(role="user", parts=[part])
        )
        return waiting

    first = asyncio.run(reply_with({"approved": "perhaps", "by": ""}))
    second = asyncio.run(reply_with({"approved": False, "by": "Asha", "reason": "recount"}))
    assert first != second and second.endswith(":1")
    with RecordStore(fresh.records) as store:
        assert store.request_state(request) is RequestState.REJECTED
        assert store.decision(request.number).note == "recount"


def test_a_run_that_is_gone_is_reported_not_resumed(fresh, tmp_path):
    request, _ = put_up(fresh, tmp_path)
    approval_flow.runs_path(fresh.records).unlink()
    with pytest.raises(RecordsError, match="is not in"):
        asyncio.run(
            approval_flow.answer(request, Answer(approved=True, by="Ravi"), records=fresh.records)
        )
    assert asyncio.run(approval_flow.planned(request, records=fresh.records)) is None


def test_an_answer_needs_a_name():
    with pytest.raises(ValueError, match="name of the person"):
        Answer(approved=True, by="  ")
