"""An approver's reply, whatever channel it came on, turned into a decision (ADR 0019).

Only people on the approvers list can answer, and they are known by phone
number, so a reply from anyone else is ignored. A reply names the request it
answers: a button carries it, and typed replies carry it too:

    approve A-0012
    reject A-0012 count it again

A rejection needs a reason, so the reject button asks for one rather than
rejecting on its own. Replying ``brief``, or tapping the button of the template
announcing one, gets the latest morning brief (ADR 0021). Every reply from an
approver is recorded once, by the channel's message id, so a message delivered
twice is answered once.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from batchward.agents import approval_flow
from batchward.agents.approval_flow import Answer, Outcome
from batchward.core.approvals import RequestState
from batchward.records.store import RecordsError, RecordStore

BRIEF = "brief"
"""What an approver replies, or a template's button sends, to get the latest brief."""

_COMMAND = re.compile(r"\s*(approve|reject)\s*[:\s]\s*(A-?\d+)\b\s*(.*)", re.IGNORECASE | re.DOTALL)


class ApproversFileError(ValueError):
    """An approvers file that cannot be read, naming the line."""


def phone_number(text: str) -> str:
    """A phone number as channels send it: digits only, with the country code."""
    return re.sub(r"\D", "", text)


def read_approvers(lines: Iterable[str]) -> dict[str, str]:
    """Who may approve, from CSV: ``phone,name``, with the country code on every number."""
    approvers: dict[str, str] = {}
    for number, row in enumerate(csv.reader(lines), start=1):
        if not row or not "".join(row).strip() or row[0].strip().lower() == "phone":
            continue
        if len(row) < 2 or not row[1].strip():
            raise ApproversFileError(f"line {number} needs a phone number and a name")
        phone = phone_number(row[0])
        if not 10 < len(phone) <= 15:
            raise ApproversFileError(
                f"line {number}: {row[0].strip()!r} is not a phone number with its country code"
            )
        approvers[phone] = row[1].strip()
    return approvers


def load_approvers(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as lines:
        return read_approvers(lines)


@dataclass(frozen=True, slots=True)
class Reply:
    """A message from a person, as a channel delivered it."""

    channel: str
    message_id: str
    sender: str
    """The sender's phone number, digits only."""
    text: str
    """What they typed, or the payload of the button they tapped."""
    at: datetime


@dataclass(frozen=True, slots=True)
class Command:
    approve: bool
    request: str
    reason: str


def command(text: str) -> Command | None:
    """What a reply asks for, if it names a request to approve or reject."""
    found = _COMMAND.fullmatch(text)
    if found is None:
        return None
    number = found[2].upper()
    if "-" not in number:
        number = f"A-{number[1:]}"
    return Command(found[1].lower() == "approve", number, found[3].strip())


async def answer_reply(reply: Reply, *, records: Path, approvers: dict[str, str]) -> str | None:
    """Act on a reply; what to send back, or None to send nothing.

    Nothing is sent to someone not on the approvers list, nor for a message
    already answered.
    """
    name = approvers.get(phone_number(reply.sender))
    if name is None:
        return None
    try:
        with RecordStore(records, create=False) as store:
            if not store.save_message(
                reply.channel, reply.message_id, reply.sender, reply.text, reply.at
            ):
                return None
            if reply.text.strip().lower() == BRIEF:
                kept = store.latest_brief()
                return kept.text if kept is not None else "No brief has been sent yet."
            asked = command(reply.text)
            if asked is None:
                return _HELP
            request = store.request(asked.request)
            if request is None:
                return f"There is no request {asked.request}."
            state = store.request_state(request)
            decision = store.decision(request.number)
    except (RecordsError, OSError) as error:
        return f"Batchward could not read its records: {error}"
    if state is not RequestState.WAITING:
        ended = f"{request.number} is {state}"
        if decision is not None:
            ended += f" (by {decision.decided_by} on {decision.at:%d/%m/%Y %H:%M})"
        return f"{ended}; there is nothing to answer."
    if not asked.approve and not asked.reason:
        return (
            f"Why reject {request.number}? Reply: reject {request.number} and the reason, "
            f"e.g. reject {request.number} count it again"
        )
    reply_to = Answer(approved=asked.approve, by=name, reason=asked.reason)
    try:
        outcome = await approval_flow.answer(request, reply_to, records=records)
    except (RecordsError, OSError) as error:
        return f"{request.number} was not answered: {error}"
    return describe(outcome)


def describe(outcome: Outcome) -> str:
    """An outcome in one message, for the person who answered."""
    if outcome.outcome == "rejected":
        return f"{outcome.request} rejected by {outcome.by}: {outcome.detail}. Nothing written."
    if outcome.outcome == "not posted":
        return f"{outcome.request} approved by {outcome.by}, but not posted: {outcome.detail}"
    if not outcome.written:
        return f"{outcome.request}: {outcome.detail}; nothing written again."
    names = ", ".join(Path(path).name for path in outcome.written)
    return f"{outcome.request} approved by {outcome.by}. Written: {names}"


_HELP = (
    "To answer a request, reply: approve A-0012, or reject A-0012 and the reason. "
    "Requests waiting are listed by `batchward approvals list`. "
    "Reply brief for the latest morning brief."
)
