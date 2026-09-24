"""Approval as a graph workflow that pauses for a person (ADR 0005).

Every action that writes anything for Marg runs as a small workflow: a node
records a numbered request for approval in the records database and pauses the
run with a ``RequestInput``; the answer resumes it, and one node carries the
action out or another records the rejection. Paused runs are kept in a SQLite
file beside the records, so the answer can come hours later from another
process. Each kind of action (receiving a delivery, making an expiry claim,
placing a purchase order) supplies only its carrying-out node; asking,
rejecting and resuming are shared. Nothing here calls a model.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.events import Event, RequestInput
from google.adk.runners import Runner
from google.adk.sessions import Session
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.adk.workflow import START, Workflow, node
from google.genai import types
from pydantic import BaseModel, ValidationError, field_validator

from batchward.agents.data import DataUnavailableError
from batchward.bridge.marg_contract import MargLayoutError
from batchward.core.approvals import ApprovalRequest, Decision, Posting
from batchward.core.clock import IST
from batchward.records.store import RecordsError, RecordStore

USER = "batchward"
REQUEST_INPUT = "adk_request_input"
"""The function name ADK gives a RequestInput, and its answer."""

UNUSABLE = (
    DataUnavailableError,
    MargLayoutError,
    RecordsError,
    OSError,
    ValueError,
    sqlite3.Error,
    ValidationError,
)
"""What carrying out an approved action may fail with, to report rather than crash on."""


class Answer(BaseModel):
    """A person's answer to a request for approval."""

    approved: bool
    by: str
    reason: str = ""

    @field_validator("by")
    @classmethod
    def _someone(cls, by: str) -> str:
        if not by.strip():
            raise ValueError("an answer needs the name of the person giving it")
        return by.strip()


@dataclass(frozen=True, slots=True)
class Outcome:
    """How a request ended."""

    request: str
    outcome: str
    """"posted", "rejected" or "not posted"."""
    by: str
    detail: str = ""
    """Why it was rejected or not posted, or who approved it first."""
    written: tuple[str, ...] = ()

    @classmethod
    def from_output(cls, output: dict[str, Any]) -> Outcome:
        return cls(**{**output, "written": tuple(output.get("written", ()))})

    def to_output(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Flow:
    """One kind of action carried out on approval."""

    kind: str
    """As recorded on its requests, e.g. "purchase voucher"."""
    app: str
    """The ADK app and workflow name; its runs are found under it."""
    carry_out: Callable[..., dict]
    """The node run on approval. Parameters other than ``node_input`` bind from state."""

    def workflow(self) -> Workflow:
        return Workflow(
            name=self.app,
            edges=[
                (
                    START,
                    node(_ask_approval, name="ask_approval", rerun_on_resume=True),
                    {
                        "approved": node(self.carry_out, name="carry_out"),
                        "rejected": node(_record_rejection, name="record_rejection"),
                    },
                )
            ],
        )

    def app_for_runs(self) -> App:
        return App(name=self.app, root_agent=self.workflow())


def flows() -> dict[str, Flow]:
    """Every kind of action that waits for approval, by kind."""
    from batchward.agents import breakage, claiming, ordering, receiving

    return {
        flow.kind: flow for flow in (receiving.FLOW, claiming.FLOW, ordering.FLOW, breakage.FLOW)
    }


async def _ask_approval(ctx: Context, kind: str, posting: dict, records: str):
    """Record the request, pause for the answer, then route on it.

    The node runs again from the top when the run resumes, so the request is found
    rather than made twice, and the answer is read from the resume inputs. An answer
    that does not read is asked for again under a new interrupt id, since ADK keeps
    an answer it could not use and would refuse every later one.
    """
    with RecordStore(records, create=False) as store:
        request = store.request_approval(
            approval_id=posting["approval_id"],
            kind=kind,
            digest=posting["digest"],
            summary=posting["summary"],
            at=datetime.now(IST),
            session_id=ctx.session.id,
        )
    attempt = 0
    while True:
        interrupt_id = f"approve:{ctx.invocation_id}:{attempt}"
        if interrupt_id not in ctx.resume_inputs:
            yield RequestInput(
                interrupt_id=interrupt_id,
                message=f"Approve {request.number}? {request.summary}",
                payload={"request": request.number, "approval_id": request.approval_id},
            )
            return
        try:
            answer = Answer.model_validate(ctx.resume_inputs[interrupt_id])
        except ValidationError:
            attempt += 1
            continue
        yield Event(
            output={"request": request.number, **answer.model_dump()},
            route="approved" if answer.approved else "rejected",
        )
        return


def _record_rejection(node_input: dict, records: str) -> dict:
    """Record the rejection and why. Nothing is written for Marg."""
    request, by, reason = node_input["request"], node_input["by"], node_input["reason"]
    problem = record(Path(records), Decision(request, False, by, datetime.now(IST), note=reason))
    return Outcome(request, "rejected", by, reason + problem).to_output()


def record(records: Path, decision: Decision) -> str:
    """Record a decision; if that fails, what to tell the person, since the run has ended."""
    try:
        with RecordStore(records) as store:
            store.save_decision(decision)
    except (RecordsError, sqlite3.Error) as error:
        return f" (the decision could not be recorded: {error})"
    return ""


def not_carried_out(records: Path, request: str, by: str, at: datetime, error: Exception) -> dict:
    """The outcome of an approved action that failed, with the decision recorded and why."""
    reason = str(error).splitlines()[0]
    reason += record(records, Decision(request, True, by, at, note=f"not posted: {reason}"))
    return Outcome(request, "not posted", by, reason).to_output()


def runs_path(records: Path) -> Path:
    """Where paused runs are kept: a SQLite file beside the records database."""
    return records.with_name(f"{records.stem}.runs.sqlite")


async def ask(kind: str, state: dict[str, Any], *, records: Path) -> ApprovalRequest:
    """Start a run asking for approval; returns the request it made.

    ``state`` holds what the run needs when it resumes: at least ``posting`` (the
    files approval would write, as a dict) and whatever the kind's node reads.
    """
    flow = flows()[kind]
    service = SqliteSessionService(str(runs_path(records)))
    session = await service.create_session(
        app_name=flow.app,
        user_id=USER,
        state={**state, "kind": kind, "records": str(records)},
    )
    await _run(flow, service, session, types.Content(role="user", parts=[types.Part(text=kind)]))
    with RecordStore(records, create=False) as store:
        request = store.request_for_session(session.id)
    if request is None:
        raise RecordsError("the approval run stopped without asking for approval")
    return request


async def answer(request: ApprovalRequest, reply: Answer, *, records: Path) -> Outcome:
    """Resume the run waiting on ``request`` with a person's answer."""
    flow = _flow(request)
    service = SqliteSessionService(str(runs_path(records)))
    session = await service.get_session(
        app_name=flow.app, user_id=USER, session_id=request.session_id
    )
    if session is None:
        raise RecordsError(f"the run waiting on {request.number} is not in {runs_path(records)}")
    waiting = pending(session)
    if waiting is None:
        raise RecordsError(f"the run for {request.number} is not waiting for an answer")
    message = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=waiting, name=REQUEST_INPUT, response=reply.model_dump()
                )
            )
        ],
    )
    output = await _run(flow, service, session, message)
    if output is None:
        raise RecordsError(f"the run for {request.number} did not finish")
    return Outcome.from_output(output)


async def planned(request: ApprovalRequest, *, records: Path) -> Posting | None:
    """What approving ``request`` would write, from its run; None if the run is gone."""
    service = SqliteSessionService(str(runs_path(records)))
    session = await service.get_session(
        app_name=_flow(request).app, user_id=USER, session_id=request.session_id
    )
    if session is None or "posting" not in session.state:
        return None
    return Posting(**session.state["posting"])


def pending(session: Session) -> str | None:
    """The interrupt id of the question a paused run waits on, if it waits on one."""
    answered = {
        response.id
        for event in session.events
        if event.author == "user"
        for response in event.get_function_responses()
    }
    waiting = None
    for event in session.events:
        for call in event.get_function_calls():
            if call.name == REQUEST_INPUT and call.id not in answered:
                waiting = call.id
    return waiting


def _flow(request: ApprovalRequest) -> Flow:
    flow = flows().get(request.kind)
    if flow is None:
        raise RecordsError(f"{request.number} is a {request.kind}, which nothing here carries out")
    return flow


async def _run(
    flow: Flow, service: SqliteSessionService, session: Session, message: types.Content
) -> dict | None:
    """Run until the workflow pauses or ends; its final output, if it ended."""
    runner = Runner(app=flow.app_for_runs(), session_service=service)
    output = None
    try:
        async for event in runner.run_async(
            user_id=USER, session_id=session.id, new_message=message
        ):
            if event.output is not None and f"{flow.app}@1" in (event.node_info.output_for or ()):
                output = event.output
    finally:
        await runner.close()
    return output
