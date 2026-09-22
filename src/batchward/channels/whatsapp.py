"""WhatsApp through Meta's Cloud API: approval buttons out, approvers' taps back (ADR 0019).

A request for approval goes to each approver as a message with two buttons,
Approve and Reject, whose payloads name the request. Inside the 24 hours after
an approver last wrote to the business number, WhatsApp allows a free-form
interactive message; outside it, only an approved template, so a template name
can be configured for approval requests, with two quick-reply buttons.

Replies come back to a webhook. Meta signs each delivery with the app secret,
and a delivery whose signature does not match is refused before it is read.
Nothing here holds a key: the access token, app secret and verify token come
from the environment, usually ``.env``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from batchward.channels.replies import Reply, phone_number
from batchward.core.approvals import ApprovalRequest

CHANNEL = "whatsapp"
GRAPH = "https://graph.facebook.com"
BODY_LIMIT = 1024
"""The most characters an interactive message's body may hold."""
API_VERSION = "v25.0"
"""The Graph API version Meta's Cloud API documentation shows; WHATSAPP_API_VERSION overrides."""
LANGUAGE = "en"


class WhatsAppError(RuntimeError):
    """WhatsApp is not configured, or refused a message."""


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    phone_number_id: str
    app_secret: str
    verify_token: str
    api_version: str = API_VERSION
    template: str | None = None
    """An approved template for approval requests, needed outside the 24-hour window."""
    language: str = LANGUAGE

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        names = {
            "token": "WHATSAPP_TOKEN",
            "phone_number_id": "WHATSAPP_PHONE_NUMBER_ID",
            "app_secret": "WHATSAPP_APP_SECRET",
            "verify_token": "WHATSAPP_VERIFY_TOKEN",
        }
        missing = [name for name in names.values() if not env.get(name, "").strip()]
        if missing:
            raise WhatsAppError(f"WhatsApp is not configured: set {', '.join(missing)} in .env")
        return cls(
            **{field: env[name].strip() for field, name in names.items()},
            api_version=env.get("WHATSAPP_API_VERSION", "").strip() or API_VERSION,
            template=env.get("WHATSAPP_APPROVAL_TEMPLATE", "").strip() or None,
            language=env.get("WHATSAPP_TEMPLATE_LANGUAGE", "").strip() or LANGUAGE,
        )

    @property
    def messages_url(self) -> str:
        return f"{GRAPH}/{self.api_version}/{self.phone_number_id}/messages"


def approval_message(
    request: ApprovalRequest, to: str, *, template: str | None = None, language: str = "en"
) -> dict[str, Any]:
    """A request for approval with Approve and Reject buttons, to one approver.

    With ``template``, the approved template is sent, its body filled with the
    request number and summary and its two quick-reply buttons given the payloads.
    """
    approve, reject = f"approve:{request.number}", f"reject:{request.number}"
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": recipient(to)}
    if template is not None:
        return {
            **base,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": request.number},
                            {"type": "text", "text": request.summary},
                        ],
                    },
                    *(
                        {
                            "type": "button",
                            "sub_type": "quick_reply",
                            "index": index,
                            "parameters": [{"type": "payload", "payload": payload}],
                        }
                        for index, payload in enumerate((approve, reject))
                    ),
                ],
            },
        }
    body = f"Approve {request.number}? {request.summary}"
    return {
        **base,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:BODY_LIMIT]},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": approve, "title": "Approve"}},
                    {"type": "reply", "reply": {"id": reject, "title": "Reject"}},
                ]
            },
        },
    }


def recipient(phone: str) -> str:
    """A number to send to, with its plus sign.

    Without one, Meta reads the number as in the business number's own country,
    which could reach the wrong person.
    """
    return f"+{phone_number(phone)}"


def text_message(to: str, text: str) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient(to),
        "type": "text",
        "text": {"body": text[:4096]},
    }


Post = Callable[[str, dict[str, str], bytes], bytes]
"""Sends a POST: (url, headers, body) to the response body."""


def _post(url: str, headers: dict[str, str], body: bytes) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise WhatsAppError(f"WhatsApp refused the message ({error.code}): {detail}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise WhatsAppError(f"WhatsApp could not be reached: {error}") from error


def send(settings: Settings, message: dict[str, Any], *, post: Post = _post) -> dict[str, Any]:
    """Send a message through the Cloud API; its reply, which names the message sent."""
    headers = {
        "Authorization": f"Bearer {settings.token}",
        "Content-Type": "application/json",
    }
    answer = post(settings.messages_url, headers, json.dumps(message).encode("utf-8"))
    try:
        return json.loads(answer or b"{}")
    except json.JSONDecodeError as error:
        raise WhatsAppError(f"WhatsApp answered with something unreadable: {error}") from error


def signed(body: bytes, signature: str | None, app_secret: str) -> bool:
    """Whether a webhook delivery carries Meta's signature of exactly this body."""
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


def challenge(params: Mapping[str, str], verify_token: str) -> str | None:
    """The answer to Meta's check that the webhook is ours, or None if it is not for us."""
    if params.get("hub.mode") != "subscribe":
        return None
    given = params.get("hub.verify_token", "").encode("utf-8")
    if not hmac.compare_digest(given, verify_token.encode("utf-8")):
        return None
    return params.get("hub.challenge")


def replies(event: Mapping[str, Any]) -> list[Reply]:
    """The messages people sent, from a webhook delivery; status updates are left out.

    A tapped reply button carries its id, a tapped template button its payload,
    and a typed message its text. Other kinds of message are left out too.
    """
    found = []
    if event.get("object") != "whatsapp_business_account":
        return found
    for entry in event.get("entry") or ():
        for change in entry.get("changes") or ():
            if change.get("field") != "messages":
                continue
            for message in (change.get("value") or {}).get("messages") or ():
                text = _text(message)
                if text is None or not message.get("id") or not message.get("from"):
                    continue
                try:
                    at = datetime.fromtimestamp(int(message.get("timestamp", 0)), UTC)
                except (TypeError, ValueError, OverflowError):
                    at = datetime.now(UTC)
                found.append(
                    Reply(CHANNEL, str(message["id"]), phone_number(str(message["from"])), text, at)
                )
    return found


def _text(message: Mapping[str, Any]) -> str | None:
    kind = message.get("type")
    if kind == "text":
        return (message.get("text") or {}).get("body")
    if kind == "button":
        return (message.get("button") or {}).get("payload")
    if kind == "interactive":
        interactive = message.get("interactive") or {}
        if interactive.get("type") == "button_reply":
            return (interactive.get("button_reply") or {}).get("id")
    return None


def notify(
    request: ApprovalRequest,
    approvers: Mapping[str, str],
    settings: Settings,
    *,
    sender: Callable[[Settings, dict[str, Any]], Any] = send,
) -> dict[str, str | None]:
    """Send a request for approval to every approver; by name, None if sent or why not."""
    results: dict[str, str | None] = {}
    for phone, name in approvers.items():
        message = approval_message(
            request, phone, template=settings.template, language=settings.language
        )
        try:
            sender(settings, message)
        except WhatsAppError as error:
            results[name] = str(error)
        else:
            results[name] = None
    return results
