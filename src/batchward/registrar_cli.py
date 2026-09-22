"""``batchward registrar``: check a Marg database's Rule 65 records as an inspector would."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from batchward.agents.data import DataUnavailableError, load_marg
from batchward.arguments import non_negative_int
from batchward.bridge.marg_contract import MargLayoutError
from batchward.bridge.marg_import import MargDataError
from batchward.compliance.registrar import keep_from, rule65_check


def add_registrar_commands(commands: argparse._SubParsersAction) -> None:
    registrar = commands.add_parser(
        "registrar",
        help="check that every sale memo and purchase record carries what Rule 65 requires",
    )
    registrar.add_argument("--marg", type=Path, required=True, help="Marg database to check")
    registrar.add_argument(
        "--since", type=date.fromisoformat, help="first day to check; default three years back"
    )
    registrar.add_argument(
        "--as-of", type=date.fromisoformat, help="last day to check; default the latest records"
    )
    registrar.add_argument(
        "--top", type=non_negative_int, default=10, help="how many findings to list"
    )
    registrar.set_defaults(handler=_registrar)


def _registrar(args: argparse.Namespace) -> int:
    try:
        stock = load_marg(args.marg)
    except (DataUnavailableError, MargDataError, MargLayoutError, ValueError) as error:
        print(f"batchward registrar: {error}", file=sys.stderr)
        return 1
    as_of = args.as_of or stock.today
    since = args.since or keep_from(as_of)
    try:
        report = rule65_check(stock.ledger, stock.parties, stock.items, since=since, as_of=as_of)
    except ValueError as error:
        print(f"batchward registrar: {error}", file=sys.stderr)
        return 1

    print(f"Rule 65 records, {since:%d/%m/%Y} to {as_of:%d/%m/%Y}")
    print(
        f"  {report.sale_memos:,} sale memos ({report.sale_lines:,} lines) and "
        f"{report.purchase_bills:,} purchase bills ({report.purchase_lines:,} lines) checked"
    )
    if report.complete:
        print("  Every memo and purchase record carries the particulars Rule 65(5) requires.")
    else:
        total = len(report.findings)
        print(f"  {total:,} {'gap' if total == 1 else 'gaps'} an inspector would find:")
        for gap, bills in report.counts().items():
            print(f"    {bills:>7,}  {gap}")
        print()
        for finding in report.findings[: args.top]:
            party = stock.parties.get(finding.party_id) if finding.party_id else None
            who = f"  {party.name if party else finding.party_id}" if finding.party_id else ""
            print(
                f"  {finding.day:%d/%m/%Y}  {finding.record:14}{finding.document_ref:20}"
                f"{finding.gap}{who}"
            )
    print()
    print(
        f"  Memos dated from {report.keep_from:%d/%m/%Y} must be kept; "
        f"{report.memos_past_retention:,} are older than three years."
    )
    print("  Not checked: the competent person's signature on each memo, which is on paper.")
    return 0
