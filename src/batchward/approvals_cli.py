"""``batchward approvals``: see what waits for approval, and approve or reject it.

An action that writes anything for Marg waits as a numbered request, its run
paused until a person answers (ADR 0005). ``approvals list`` shows what waits,
``approvals show`` what approving one would write, and ``approvals approve`` or
``approvals reject`` resume its run with the answer. ``intake receive --out``
puts a delivery up for approval, and ``claims draft`` an expiry claim, through
the same functions.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from batchward.agents import approval_flow
from batchward.agents.approval_flow import Answer, Outcome
from batchward.core.approvals import ApprovalRequest, Posting, RequestState
from batchward.records.store import RecordsError, RecordStore

_FAILURES = (RecordsError, sqlite3.Error, OSError)


def add_approval_commands(commands: argparse._SubParsersAction) -> None:
    approvals = commands.add_parser("approvals", help="see and answer requests for approval")
    actions = approvals.add_subparsers(dest="approvals_command", required=True)

    listing = actions.add_parser("list", help="requests waiting for approval")
    _records(listing)
    listing.add_argument("--all", action="store_true", help="every request, however it ended")
    listing.set_defaults(handler=_list)

    show = actions.add_parser("show", help="a request, and what approving it would write")
    show.add_argument("request", help="the request number, e.g. A-0001")
    _records(show)
    show.set_defaults(handler=_show)

    approve = actions.add_parser("approve", help="approve a request, carrying out its action")
    approve.add_argument("request", help="the request number, e.g. A-0001")
    _records(approve)
    approve.add_argument("--by", required=True, help="the person approving")
    approve.set_defaults(handler=_approve)

    reject = actions.add_parser("reject", help="reject a request; nothing is written")
    reject.add_argument("request", help="the request number, e.g. A-0001")
    _records(reject)
    reject.add_argument("--by", required=True, help="the person rejecting")
    reject.add_argument("--reason", required=True, help="why, for the record")
    reject.set_defaults(handler=_reject)


def _records(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--records", type=Path, required=True, help="Batchward records database")


def request_approval(
    planned: Posting,
    *,
    records: Path,
    start: Callable[[], Awaitable[ApprovalRequest]],
    approve_by: str | None,
    notify: Callable[[ApprovalRequest], int] | None = None,
) -> int:
    """Put a posting up for approval, or find it already waiting; answer it if approved now.

    ``start`` starts the run that asks, when nothing the same is already waiting.
    ``notify`` sends the waiting request to the approvers, as on WhatsApp.
    """
    try:
        with RecordStore(records, create=False) as store:
            first = store.approval(planned.approval_id)
            waiting = [
                request
                for request in store.requests()
                if request.approval_id == planned.approval_id
                and store.request_state(request) is RequestState.WAITING
            ]
    except _FAILURES as error:
        return _fail(error)
    if first is not None:
        if first.digest == planned.digest:
            print(
                f"Already approved by {first.approved_by} on {first.at:%d/%m/%Y %H:%M}; "
                "nothing written again."
            )
            return 0
        return _fail(
            f"{planned.approval_id} was already approved by {first.approved_by} on "
            f"{first.at:%d/%m/%Y %H:%M}, in a different form"
        )

    same = next((request for request in waiting if request.digest == planned.digest), None)
    if same is not None:
        request = same
        print(f"Already waiting for approval as {request.number}.")
    else:
        try:
            request = asyncio.run(start())
        except _FAILURES as error:
            return _fail(error)
        for replaced in waiting:
            print(f"{replaced.number}, waiting for the same in another form, is replaced.")
        print(f"Put up for approval as {request.number}: {request.summary}.")
    if approve_by is None:
        print(
            f"Approve with `batchward approvals approve {request.number} --records "
            f"{records} --by <name>`, or reject it with `approvals reject`."
        )
        return 0 if notify is None else notify(request)
    return _answer(request, Answer(approved=True, by=approve_by), records)


def _list(args: argparse.Namespace) -> int:
    try:
        with RecordStore(args.records, create=False) as store:
            rows = [(request, store.request_state(request)) for request in store.requests()]
            decisions = {request.number: store.decision(request.number) for request, _ in rows}
    except _FAILURES as error:
        return _fail(error)
    if not args.all:
        rows = [(request, state) for request, state in rows if state is RequestState.WAITING]
        if not rows:
            print("Nothing is waiting for approval.")
            return 0
        print("Waiting for approval:")
    for request, state in rows:
        line = f"  {request.number}  {request.requested_at:%d/%m/%Y %H:%M}  {request.summary}"
        if args.all:
            decision = decisions[request.number]
            by = f" by {decision.decided_by}" if decision is not None else ""
            line += f"  [{state}{by}]"
        print(line)
    return 0


def _show(args: argparse.Namespace) -> int:
    try:
        request, state, decision = _find(args)
        planned = asyncio.run(approval_flow.planned(request, records=args.records))
    except _FAILURES as error:
        return _fail(error)
    print(f"{request.number}: {request.kind}, {request.summary}")
    print(f"Asked for on {request.requested_at:%d/%m/%Y %H:%M}. Now: {state}.")
    if decision is not None:
        verb = "Approved" if decision.approved else "Rejected"
        print(f"{verb} by {decision.decided_by} on {decision.at:%d/%m/%Y %H:%M}.")
        if decision.note:
            print(f"Note: {decision.note}")
    if planned is not None:
        for name, text in planned.files.items():
            print(f"\n--- {name}")
            print(text, end="" if text.endswith("\n") else "\n")
    return 0


def _approve(args: argparse.Namespace) -> int:
    return _decide(args, Answer(approved=True, by=args.by))


def _reject(args: argparse.Namespace) -> int:
    if not args.reason.strip():
        return _fail("a rejection needs a reason")
    return _decide(args, Answer(approved=False, by=args.by, reason=args.reason.strip()))


def _decide(args: argparse.Namespace, reply: Answer) -> int:
    try:
        request, state, decision = _find(args)
    except _FAILURES as error:
        return _fail(error)
    if state is not RequestState.WAITING:
        ended = f"{request.number} is {state}"
        if decision is not None:
            ended += f" (by {decision.decided_by} on {decision.at:%d/%m/%Y %H:%M})"
        return _fail(f"{ended}; there is nothing to answer")
    return _answer(request, reply, args.records)


def _find(args: argparse.Namespace):
    with RecordStore(args.records, create=False) as store:
        request = store.request(args.request)
        if request is None:
            raise RecordsError(f"no request {args.request} in {args.records}")
        return request, store.request_state(request), store.decision(request.number)


def _answer(request: ApprovalRequest, reply: Answer, records: Path) -> int:
    try:
        outcome = asyncio.run(approval_flow.answer(request, reply, records=records))
    except _FAILURES as error:
        return _fail(error)
    return _print_outcome(outcome)


def _print_outcome(outcome: Outcome) -> int:
    if outcome.outcome == "rejected":
        print(f"{outcome.request} rejected by {outcome.by}: {outcome.detail}. Nothing written.")
        return 0
    if outcome.outcome == "not posted":
        return _fail(
            f"{outcome.request} approved by {outcome.by}, but not posted: {outcome.detail}"
        )
    if not outcome.written:
        print(f"{outcome.detail[:1].upper()}{outcome.detail[1:]}; nothing written again.")
        return 0
    print(f"Approved by {outcome.by}. Written, to import into Marg or to send:")
    for path in outcome.written:
        print(f"  {path}")
    return 0


def _fail(error: object) -> int:
    print(f"batchward approvals: {error}", file=sys.stderr)
    return 1
