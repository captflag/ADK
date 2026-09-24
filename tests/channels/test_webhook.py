import hashlib
import hmac
import json
import shutil
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from batchward.channels.webhook import create_app
from batchward.channels.whatsapp import Settings, WhatsAppError
from batchward.cli import main
from batchward.core.approvals import RequestState
from batchward.records.store import KeptBrief, RecordStore

SETTINGS = Settings(
    token="test-token",
    phone_number_id="1234567890",
    app_secret="test-secret",
    verify_token="test-verify",
)
RAVI, STRANGER = "919812345678", "919000000009"


@pytest.fixture(scope="module")
def deliveries(tmp_path_factory):
    folder = tmp_path_factory.mktemp("whatsapp")
    demo = ["demo-marg", str(folder / "marg.sqlite"), "--start", "2026-01-01", "--days", "30"]
    options = ["--chemists", "40", "--invoices", str(folder / "inv")]
    assert main([*demo, *options, "--records", str(folder / "rec.sqlite")]) == 0
    return folder


@pytest.fixture
def waiting(deliveries, tmp_path):
    """Records with a delivery waiting for approval as A-0001, and who may approve."""
    records = tmp_path / "rec.sqlite"
    shutil.copy(deliveries / "rec.sqlite", records)
    reading = sorted(p for p in (deliveries / "inv").glob("*.json") if ".order" not in p.name)[0]
    order_no = json.loads(reading.read_text(encoding="utf-8"))["order_no"]
    receive = [
        "intake", "receive", str(reading), "--marg", str(deliveries / "marg.sqlite"),
        "--records", str(records), "--count", str(reading.with_suffix(".count.csv")),
        "--order", str(deliveries / "inv" / (order_no.replace("/", "-") + ".order.json")),
        "--received", "2026-01-30", "--out", str(tmp_path / "posted"),
    ]  # fmt: skip
    assert main(receive) == 0
    approvers = tmp_path / "approvers.csv"
    approvers.write_text(f"phone,name\n+{RAVI},Ravi (owner)\n", encoding="utf-8")
    return records, approvers, tmp_path / "posted"


@pytest.fixture
def webhook(waiting):
    records, approvers, _ = waiting
    sent = []
    app = create_app(
        SETTINGS,
        records=records,
        approvers=approvers,
        sender=lambda settings, message: sent.append(message),
    )
    return TestClient(app), sent


def post(client, *messages, secret="test-secret"):
    event = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "messages": list(messages)}}]}],
    }  # fmt: skip
    body = json.dumps(event).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )


def tap(payload, *, sender=RAVI, message_id="wamid.1"):
    return {
        "from": sender,
        "id": message_id,
        "timestamp": "1769767200",
        "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": payload, "title": "x"}},
    }


def typed(text, *, sender=RAVI, message_id="wamid.2"):
    return {
        "from": sender,
        "id": message_id,
        "timestamp": "1769767260",
        "type": "text",
        "text": {"body": text},
    }


def state(records):
    with RecordStore(records, create=False) as store:
        (request,) = store.requests()
        return store.request_state(request), store.decision(request.number)


def test_meta_checks_the_webhook_with_the_verify_token(webhook):
    client, _ = webhook
    query = {"hub.mode": "subscribe", "hub.verify_token": "test-verify", "hub.challenge": "1158"}
    answer = client.get("/whatsapp", params=query)
    assert (answer.status_code, answer.text) == (200, "1158")
    assert client.get("/whatsapp", params={**query, "hub.verify_token": "no"}).status_code == 403


def test_a_tap_on_approve_posts_the_delivery_and_says_so(webhook, waiting):
    client, sent = webhook
    records, _, posted = waiting
    assert post(client, tap("approve:A-0001")).status_code == 200
    ((reply,),) = [sent]
    assert reply["to"] == f"+{RAVI}"
    assert reply["text"]["body"].startswith("A-0001 approved by Ravi")
    assert sorted(p.suffix for p in posted.iterdir()) == [".csv"]
    assert state(records)[0] is RequestState.APPROVED

    assert post(client, tap("approve:A-0001")).status_code == 200  # delivered again
    assert len(sent) == 1
    assert post(client, tap("approve:A-0001", message_id="wamid.9")).status_code == 200
    assert "A-0001 is approved (by Ravi (owner)" in sent[-1]["text"]["body"]


def test_reject_asks_why_and_a_typed_reason_rejects(webhook, waiting):
    client, sent = webhook
    records, _, posted = waiting
    post(client, tap("reject:A-0001"))
    assert sent[-1]["text"]["body"].startswith("Why reject A-0001?")
    assert state(records)[0] is RequestState.WAITING
    post(client, typed("Reject A-0001 count it again"))
    assert (
        sent[-1]["text"]["body"]
        == "A-0001 rejected by Ravi (owner): count it again. Nothing written."
    )
    request_state, decision = state(records)
    assert (request_state, decision.note) == (RequestState.REJECTED, "count it again")
    assert not posted.exists()


def test_strangers_and_forged_deliveries_are_ignored(webhook, waiting):
    client, sent = webhook
    records, _, _ = waiting
    assert post(client, tap("approve:A-0001", sender=STRANGER)).status_code == 200
    assert post(client, tap("approve:A-0001"), secret="guessed").status_code == 401
    assert sent == [] and state(records)[0] is RequestState.WAITING


def test_a_message_that_names_no_request_gets_the_help(webhook):
    client, sent = webhook
    post(client, typed("ok"))
    post(client, typed("approve A-0404", message_id="wamid.3"))
    assert sent[0]["text"]["body"].startswith("To answer a request, reply: approve A-0012")
    assert sent[1]["text"]["body"] == "There is no request A-0404."


def test_a_reply_that_cannot_be_sent_does_not_stop_the_webhook(waiting):
    records, approvers, _ = waiting

    def refuse(settings, message):
        raise WhatsAppError("WhatsApp could not be reached: offline")

    client = TestClient(create_app(SETTINGS, records=records, approvers=approvers, sender=refuse))
    assert post(client, tap("approve:A-0001")).status_code == 200
    assert state(records)[0] is RequestState.APPROVED


def test_asking_for_the_brief_gets_the_latest_one_kept(webhook, waiting):
    client, sent = webhook
    records, _, _ = waiting
    post(client, typed("Brief"))
    assert sent[-1]["text"]["body"] == "No brief has been sent yet."
    with RecordStore(records) as store:
        for day in (30, 31):
            store.save_brief(
                KeptBrief(date(2026, 1, day), datetime(2026, 1, day, 2, 30, tzinfo=UTC),
                          f"Batchward brief for {day}/01/2026")
            )  # fmt: skip
    button = {"from": RAVI, "id": "wamid.5", "timestamp": "1769767300", "type": "button",
              "button": {"payload": "brief", "text": "Show brief"}}  # fmt: skip
    post(client, button)
    assert sent[-1]["text"]["body"] == "Batchward brief for 31/01/2026"
    assert state(records)[0] is RequestState.WAITING
