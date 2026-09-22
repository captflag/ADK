"""The webhook WhatsApp delivers approvers' replies to (ADR 0019).

Meta checks the webhook once with a GET carrying the verify token, then POSTs
every message and status update, signed with the app secret. A POST is answered
at once, and its replies are handled after, so a slow approval (matching a
delivery again reads Marg) never makes Meta deliver the same message again; if
it does, the message is recognised by its id and answered once.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request, Response

from batchward.channels.replies import ApproversFileError, answer_reply, load_approvers
from batchward.channels.whatsapp import (
    Settings,
    WhatsAppError,
    challenge,
    replies,
    send,
    signed,
    text_message,
)

logger = logging.getLogger("batchward.whatsapp")

Sender = Callable[[Settings, dict[str, Any]], Any]


def create_app(
    settings: Settings, *, records: Path, approvers: Path, sender: Sender = send
) -> FastAPI:
    """The webhook, answering for the approvers listed in ``approvers`` against ``records``.

    The approvers file is read for every delivery, so a change to it needs no restart.
    """
    app = FastAPI(title="Batchward WhatsApp webhook", docs_url=None, redoc_url=None)

    @app.get("/whatsapp")
    async def verify(request: Request) -> Response:
        answer = challenge(dict(request.query_params), settings.verify_token)
        if answer is None:
            return Response(status_code=403)
        return Response(content=answer, media_type="text/plain")

    @app.post("/whatsapp")
    async def receive(request: Request, background: BackgroundTasks) -> Response:
        body = await request.body()
        if not signed(body, request.headers.get("x-hub-signature-256"), settings.app_secret):
            logger.warning("refused a delivery whose signature does not match")
            return Response(status_code=401)
        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            return Response(status_code=400)
        background.add_task(_handle, event, settings, records, approvers, sender)
        return Response(status_code=200)

    return app


async def _handle(
    event: dict, settings: Settings, records: Path, approvers: Path, sender: Sender
) -> None:
    try:
        known = load_approvers(approvers)
    except (OSError, ApproversFileError) as error:
        logger.error("cannot read the approvers list %s: %s", approvers, error)
        return
    for reply in replies(event):
        text = await answer_reply(reply, records=records, approvers=known)
        if text is None:
            continue
        try:
            await asyncio.to_thread(sender, settings, text_message(reply.sender, text))
        except WhatsAppError as error:
            logger.error("could not answer %s: %s", reply.sender, error)
