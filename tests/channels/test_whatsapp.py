import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from batchward.channels.replies import (
    ApproversFileError,
    Command,
    command,
    phone_number,
    read_approvers,
)
from batchward.channels.whatsapp import (
    Settings,
    WhatsAppError,
    approval_message,
    challenge,
    notify,
    replies,
    send,
    signed,
    text_message,
)
from batchward.core.approvals import ApprovalRequest

SETTINGS = Settings(
    token="test-token",
    phone_number_id="1234567890",
    app_secret="test-secret",
    verify_token="test-verify",
)
REQUEST = ApprovalRequest(
    number="A-0007",
    approval_id="purchase:C06:ARA/25-26/00001",
    kind="purchase voucher",
    digest="0" * 64,
    summary="bill ARA/25-26/00001: 200 units on 1 line",
    requested_at=datetime(2026, 1, 30, 10, tzinfo=UTC),
    session_id="s1",
)


def test_an_approval_request_carries_approve_and_reject_buttons_naming_it():
    message = approval_message(REQUEST, "919812345678")
    assert (message["messaging_product"], message["to"], message["type"]) == (
        "whatsapp",
        "+919812345678",
        "interactive",
    )
    interactive = message["interactive"]
    assert interactive["type"] == "button"
    assert interactive["body"]["text"].startswith("Approve A-0007? bill ARA/25-26/00001")
    buttons = [(b["reply"]["id"], b["reply"]["title"]) for b in interactive["action"]["buttons"]]
    assert buttons == [("approve:A-0007", "Approve"), ("reject:A-0007", "Reject")]


def test_outside_the_24_hour_window_an_approved_template_is_sent_instead():
    message = approval_message(REQUEST, "919812345678", template="batchward_approval")
    template = message["template"]
    assert (message["type"], template["name"], template["language"]) == (
        "template",
        "batchward_approval",
        {"code": "en"},
    )
    body, approve, reject = template["components"]
    assert [p["text"] for p in body["parameters"]] == ["A-0007", REQUEST.summary]
    assert [
        (b["sub_type"], b["index"], b["parameters"][0]["payload"]) for b in (approve, reject)
    ] == [
        ("quick_reply", 0, "approve:A-0007"),
        ("quick_reply", 1, "reject:A-0007"),
    ]


def test_a_long_summary_is_cut_to_what_whatsapp_accepts():
    long = replace(REQUEST, summary="x" * 2000)
    assert len(approval_message(long, "919812345678")["interactive"]["body"]["text"]) == 1024


def test_a_message_is_posted_to_the_phone_numbers_endpoint_with_the_token():
    posted = []

    def post(url, headers, body):
        posted.append((url, headers, json.loads(body)))
        return b'{"messages": [{"id": "wamid.1"}]}'

    answer = send(SETTINGS, text_message("919812345678", "hello"), post=post)
    assert answer == {"messages": [{"id": "wamid.1"}]}
    ((url, headers, body),) = posted
    assert url == "https://graph.facebook.com/v25.0/1234567890/messages"
    assert headers["Authorization"] == "Bearer test-token"
    assert body["text"] == {"body": "hello"}


def test_every_approver_is_sent_the_request_and_failures_are_named():
    def sender(settings, message):
        if message["to"] == "+919000000002":
            raise WhatsAppError("WhatsApp refused the message (400): not a valid recipient")

    results = notify(
        REQUEST, {"919000000001": "Ravi", "919000000002": "Asha"}, SETTINGS, sender=sender
    )
    assert results == {
        "Ravi": None,
        "Asha": "WhatsApp refused the message (400): not a valid recipient",
    }


def test_settings_come_from_the_environment_and_name_what_is_missing():
    env = {
        "WHATSAPP_TOKEN": "t",
        "WHATSAPP_PHONE_NUMBER_ID": "1",
        "WHATSAPP_APP_SECRET": "s",
        "WHATSAPP_VERIFY_TOKEN": "v",
        "WHATSAPP_APPROVAL_TEMPLATE": "batchward_approval",
    }
    settings = Settings.from_env(env)
    assert (settings.template, settings.api_version, settings.language) == (
        "batchward_approval",
        "v25.0",
        "en",
    )
    with pytest.raises(WhatsAppError, match="set WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN"):
        Settings.from_env({"WHATSAPP_TOKEN": "t", "WHATSAPP_PHONE_NUMBER_ID": "1"})


def signature(body: bytes, secret: str = "test-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_only_a_delivery_signed_with_the_app_secret_is_accepted():
    body = b'{"object": "whatsapp_business_account"}'
    assert signed(body, signature(body), "test-secret")
    assert not signed(body + b" ", signature(body), "test-secret")
    assert not signed(body, signature(body, "other"), "test-secret")
    assert not signed(body, signature(body).removeprefix("sha256="), "test-secret")
    assert not signed(body, None, "test-secret")


def test_the_webhook_check_is_answered_only_with_the_verify_token():
    params = {"hub.mode": "subscribe", "hub.verify_token": "test-verify", "hub.challenge": "42"}
    assert challenge(params, "test-verify") == "42"
    assert challenge({**params, "hub.verify_token": "guess"}, "test-verify") is None
    assert challenge({**params, "hub.verify_token": "पासवर्ड"}, "test-verify") is None
    assert challenge({**params, "hub.mode": "unsubscribe"}, "test-verify") is None


def delivery(*messages, statuses=()):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "1234567890"},
                            "messages": list(messages),
                            "statuses": list(statuses),
                        },
                    }
                ],
            }
        ],
    }


def message(kind, content, *, sender="919812345678", message_id="wamid.A"):
    return {
        "from": sender,
        "id": message_id,
        "timestamp": "1769767200",
        "type": kind,
        kind: content,
    }


def test_taps_and_typed_replies_are_read_and_status_updates_left_out():
    event = delivery(
        message("interactive", {"type": "button_reply", "button_reply": {
            "id": "approve:A-0007", "title": "Approve"}}, message_id="m1"),
        message("button", {"payload": "reject:A-0007", "text": "Reject"}, message_id="m2"),
        message("text", {"body": "reject A-0007 count it again"}, message_id="m3"),
        message("image", {"id": "media"}, message_id="m4"),
        statuses=[{"id": "wamid.X", "status": "delivered"}],
    )  # fmt: skip
    found = replies(event)
    assert [(r.message_id, r.sender, r.text) for r in found] == [
        ("m1", "919812345678", "approve:A-0007"),
        ("m2", "919812345678", "reject:A-0007"),
        ("m3", "919812345678", "reject A-0007 count it again"),
    ]
    assert found[0].at == datetime.fromtimestamp(1769767200, UTC)
    assert replies({"object": "page", "entry": event["entry"]}) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("approve:A-0007", Command(True, "A-0007", "")),
        ("Approve a-7", Command(True, "A-7", "")),
        ("APPROVE A0012", Command(True, "A-0012", "")),
        ("reject A-0007 count it\nagain", Command(False, "A-0007", "count it\nagain")),
        ("please approve A-0007", None),
        ("approve", None),
        ("hello", None),
    ],
)
def test_a_reply_asks_to_approve_or_reject_a_named_request(text, expected):
    assert command(text) == expected


def test_approvers_are_read_by_phone_number_with_its_country_code():
    lines = ["phone,name", "+91 98123 45678,Ravi (owner)", "", "91-90000-00002 , Asha"]
    assert read_approvers(lines) == {"919812345678": "Ravi (owner)", "919000000002": "Asha"}
    assert phone_number("+91 (981) 234-5678") == "919812345678"
    with pytest.raises(ApproversFileError, match="line 1: '98123' is not a phone number"):
        read_approvers(["98123,Ravi"])
    with pytest.raises(ApproversFileError, match="line 1 needs a phone number and a name"):
        read_approvers(["919812345678"])
