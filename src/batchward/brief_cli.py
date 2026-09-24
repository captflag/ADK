"""``batchward brief``: the morning brief, printed or sent on WhatsApp (ADR 0021).

``batchward brief`` works out what to act on today from Marg and the records and
prints it. With ``--send`` the brief is kept in the records and sent to every
approver on WhatsApp, followed by each request still waiting for approval with
its Approve and Reject buttons. Batchward does not wake itself; run it each
morning from Task Scheduler or cron, with the settings in ``.env``:

    uv run --env-file .env batchward brief --send
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from batchward.agents.data import DataUnavailableError, load_marg
from batchward.arguments import env_path, non_negative_int, positive_int
from batchward.bridge.marg_contract import MargLayoutError
from batchward.buying.suggest import LEAD_DAYS, Policy
from batchward.channels.replies import ApproversFileError, load_approvers
from batchward.channels.whatsapp import (
    Settings,
    WhatsAppError,
    last_heard,
    notify,
    send_brief,
)
from batchward.records.store import KeptBrief, RecordsError, RecordStore
from batchward.reporting.brief import MOST_WAITING, Brief, morning_brief

_FAILURES = (
    DataUnavailableError,
    MargLayoutError,
    RecordsError,
    OSError,
    ValueError,
    sqlite3.Error,
)


def add_brief_commands(commands: argparse._SubParsersAction) -> None:
    brief = commands.add_parser("brief", help="the morning brief: what to act on today")
    brief.add_argument(
        "--marg",
        type=Path,
        default=env_path("BATCHWARD_MARG_DB"),
        help="Marg database (default BATCHWARD_MARG_DB)",
    )
    brief.add_argument(
        "--records",
        type=Path,
        default=env_path("BATCHWARD_RECORDS"),
        help="Batchward records database (default BATCHWARD_RECORDS)",
    )
    brief.add_argument(
        "--cover-days",
        type=positive_int,
        help="one cover for every item, in days of demand (default: each item's by its class)",
    )
    brief.add_argument(
        "--lead-days",
        type=non_negative_int,
        default=LEAD_DAYS,
        help=f"days for stock to arrive ({LEAD_DAYS})",
    )
    brief.add_argument(
        "--send", action="store_true", help="keep it and send it to the approvers on WhatsApp"
    )
    brief.add_argument(
        "--approvers",
        type=Path,
        default=env_path("BATCHWARD_APPROVERS"),
        help="CSV of phone,name to send it to (default BATCHWARD_APPROVERS)",
    )
    brief.set_defaults(handler=_brief)


def _brief(args: argparse.Namespace) -> int:
    if args.marg is None:
        return _fail("give --marg or set BATCHWARD_MARG_DB")
    sending = None
    if args.send:
        if args.records is None:
            return _fail(
                "sending needs --records or BATCHWARD_RECORDS: the brief is kept there, and "
                "the messages there say who can be sent it"
            )
        if args.approvers is None:
            return _fail("sending needs --approvers or BATCHWARD_APPROVERS: who to send it to")
        try:
            sending = Settings.from_env(), load_approvers(args.approvers)
        except (WhatsAppError, ApproversFileError, OSError) as error:
            return _fail(error)
        if not sending[1]:
            return _fail(f"{args.approvers} lists nobody to send it to")

    policy = Policy(cover_days=args.cover_days, lead_days=args.lead_days)
    try:
        stock = load_marg(args.marg)
        if args.records is None:
            brief = morning_brief(stock, None, policy=policy)
        else:
            with RecordStore(args.records, create=False) as store:
                brief = morning_brief(stock, store, policy=policy)
    except _FAILURES as error:
        return _fail(error)
    text = brief.text()
    print(text)
    if sending is None:
        return 0
    settings, people = sending
    return _send(brief, text, args.records, people, settings)


def _send(
    brief: Brief, text: str, records: Path, people: dict[str, str], settings: Settings
) -> int:
    now = datetime.now(UTC)
    try:
        with RecordStore(records, create=False) as store:
            store.save_brief(KeptBrief(brief.on, now, text))
            heard = last_heard(store.messages())
            requests = [store.request(waiting.number) for waiting in brief.waiting[:MOST_WAITING]]
    except _FAILURES as error:
        return _fail(error)

    print()
    results = send_brief(brief.on, text, brief.headline(), people, settings, heard=heard, now=now)
    for name, problem in results.items():
        print(f"  {name}: {'sent' if problem is None else problem}")
    reached = sum(problem is None for problem in results.values())
    print(f"Sent the brief to {reached} of {len(results)} approvers on WhatsApp.")

    for request in requests:
        if request is None:
            continue
        outcome = notify(request, people, settings, heard=heard, now=now)
        sent = sum(problem is None for problem in outcome.values())
        print(f"Sent {request.number}, with its buttons, to {sent} of {len(outcome)} approvers.")
        for name, problem in outcome.items():
            if problem is not None:
                print(f"  {name}: {problem}")
    return 0 if reached else 1


def _fail(error: object) -> int:
    print(f"batchward brief: {error}", file=sys.stderr)
    return 1
