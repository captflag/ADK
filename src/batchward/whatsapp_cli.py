"""``batchward whatsapp``: approvals on WhatsApp (ADR 0019).

``whatsapp serve`` runs the webhook WhatsApp delivers approvers' replies to.
``whatsapp notify`` sends a request waiting for approval to every approver, with
Approve and Reject buttons; ``intake receive --notify`` and ``claims draft
--notify`` do the same as they put something up for approval. The access token,
app secret and verify token are read from the environment, usually ``.env``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from batchward.channels.replies import ApproversFileError, load_approvers
from batchward.channels.whatsapp import Settings, WhatsAppError, notify
from batchward.core.approvals import ApprovalRequest, RequestState
from batchward.records.store import RecordsError, RecordStore


def add_whatsapp_commands(commands: argparse._SubParsersAction) -> None:
    whatsapp = commands.add_parser("whatsapp", help="approvals on WhatsApp")
    actions = whatsapp.add_subparsers(dest="whatsapp_command", required=True)

    serve = actions.add_parser("serve", help="run the webhook WhatsApp sends replies to")
    _where(serve)
    serve.add_argument("--host", default="127.0.0.1", help="address to listen on")
    serve.add_argument("--port", type=int, default=8080, help="port to listen on")
    serve.set_defaults(handler=_serve)

    send = actions.add_parser("notify", help="send a request waiting for approval to approvers")
    send.add_argument("request", help="the request number, e.g. A-0001")
    _where(send)
    send.set_defaults(handler=_notify)


def _where(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--records",
        type=Path,
        default=_env_path("BATCHWARD_RECORDS"),
        help="Batchward records database (default BATCHWARD_RECORDS)",
    )
    parser.add_argument(
        "--approvers",
        type=Path,
        default=_env_path("BATCHWARD_APPROVERS"),
        help="CSV of phone,name for who may approve (default BATCHWARD_APPROVERS)",
    )


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


def _serve(args: argparse.Namespace) -> int:
    try:
        settings = Settings.from_env()
        _check(args)
        load_approvers(args.approvers)
    except (WhatsAppError, ApproversFileError, OSError, ValueError) as error:
        return _fail(error)
    import uvicorn

    from batchward.channels.webhook import create_app

    app = create_app(settings, records=args.records, approvers=args.approvers)
    print(f"Listening on http://{args.host}:{args.port}/whatsapp for approvers' replies.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _notify(args: argparse.Namespace) -> int:
    try:
        _check(args)
        with RecordStore(args.records, create=False) as store:
            request = store.request(args.request)
            if request is None:
                raise RecordsError(f"no request {args.request} in {args.records}")
            if store.request_state(request) is not RequestState.WAITING:
                raise RecordsError(f"{request.number} is not waiting for approval")
    except (RecordsError, OSError, ValueError) as error:
        return _fail(error)
    return notify_approvers(request, approvers=args.approvers)


def notify_approvers(request: ApprovalRequest, *, approvers: Path | None) -> int:
    """Send a request to every approver on WhatsApp, saying who it reached."""
    if approvers is None:
        return _fail("sending needs --approvers or BATCHWARD_APPROVERS: who may approve")
    try:
        settings = Settings.from_env()
        people = load_approvers(approvers)
    except (WhatsAppError, ApproversFileError, OSError) as error:
        return _fail(error)
    if not people:
        return _fail(f"{approvers} lists nobody who may approve")
    results = notify(request, people, settings)
    for name, problem in results.items():
        print(f"  {name}: {'sent' if problem is None else problem}")
    sent = sum(problem is None for problem in results.values())
    print(f"Sent {request.number} to {sent} of {len(results)} approvers on WhatsApp.")
    return 0 if sent else 1


def notifier(args: argparse.Namespace):
    """With ``--notify``, what sends a request put up for approval to the approvers."""
    if not getattr(args, "notify", False):
        return None
    approvers = getattr(args, "approvers", None) or _env_path("BATCHWARD_APPROVERS")
    return lambda request: notify_approvers(request, approvers=approvers)


def _check(args: argparse.Namespace) -> None:
    if args.records is None:
        raise ValueError("give --records or set BATCHWARD_RECORDS")
    if args.approvers is None:
        raise ValueError("give --approvers or set BATCHWARD_APPROVERS: who may approve")


def _fail(error: object) -> int:
    print(f"batchward whatsapp: {error}", file=sys.stderr)
    return 1
