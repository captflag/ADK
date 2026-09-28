"""``batchward evals``: put the agent team through its cases and score it (ADR 0027).

Every case is a real question to a real model, so a run costs money and needs a
network: fill in ``GOOGLE_API_KEY`` in ``.env`` and run

    uv run --env-file .env batchward evals --marg sim-out/marg.sqlite \
        --records sim-out/records.sqlite

The cases are written against the simulated stockist, so run them on the demo
databases rather than on a real office's data. The command answers 0 only when
every case passed, so it can gate a change to an agent's instructions.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from batchward.agents.data import DataUnavailableError, load_marg, use
from batchward.arguments import env_path, positive_int
from batchward.bridge.marg_contract import MargLayoutError
from batchward.evals.cases import CASES, chosen
from batchward.evals.run import SECONDS, run_cases
from batchward.records.store import RecordsError

KEY = "GOOGLE_API_KEY"


def add_eval_commands(commands: argparse._SubParsersAction) -> None:
    evals = commands.add_parser(
        "evals", help="ask the agent team every eval case and score what it answers"
    )
    evals.add_argument(
        "--marg",
        type=Path,
        default=env_path("BATCHWARD_MARG_DB"),
        help="Marg database to answer from (default BATCHWARD_MARG_DB)",
    )
    evals.add_argument(
        "--records",
        type=Path,
        default=env_path("BATCHWARD_RECORDS"),
        help="Batchward records database (default BATCHWARD_RECORDS)",
    )
    evals.add_argument(
        "--case",
        dest="cases",
        action="append",
        metavar="NAME",
        help="ask only this case; may be given more than once",
    )
    evals.add_argument("--model", help="the model to judge (default BATCHWARD_MODEL)")
    evals.add_argument(
        "--seconds",
        type=positive_int,
        default=int(SECONDS),
        help=f"how long one case may take (default {int(SECONDS)})",
    )
    evals.add_argument("--out", type=Path, help="write the run to this file as JSON")
    evals.add_argument(
        "--list", action="store_true", help="list the cases and what each one checks, and stop"
    )
    evals.set_defaults(handler=_evals)


def _evals(args: argparse.Namespace) -> int:
    if args.list:
        return _list_cases()
    if args.marg is None:
        print(
            "batchward evals: no Marg database given; pass --marg or set BATCHWARD_MARG_DB",
            file=sys.stderr,
        )
        return 1
    if not os.environ.get(KEY):
        print(
            f"batchward evals: {KEY} is not set, and every case is a real question to the "
            f"model. Put the key in .env and run with `uv run --env-file .env`.",
            file=sys.stderr,
        )
        return 1
    try:
        data = load_marg(args.marg)
    except (DataUnavailableError, MargLayoutError, RecordsError, OSError, sqlite3.Error) as error:
        print(f"batchward evals: {error}", file=sys.stderr)
        return 1

    with use(data, records=args.records):
        try:
            asked, left_out = chosen(args.cases)
        except ValueError as error:
            print(f"batchward evals: {error}", file=sys.stderr)
            return 1
        for case, why in left_out:
            print(f"  skip  {case.name:<16} {why}")
        if not asked:
            print("batchward evals: no case can be asked of this data", file=sys.stderr)
            return 1
        print(f"Asking {len(asked)} cases as of {data.today:%d/%m/%Y}. Each one calls the model.")
        report = run_cases(asked, model=args.model, seconds=float(args.seconds))

    print(report.text())
    if args.out:
        try:
            args.out.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
        except OSError as error:
            print(f"batchward evals: cannot write {args.out}: {error}", file=sys.stderr)
            return 1
        print(f"Written to {args.out}.")
    return 0 if not report.failed else 1


def _list_cases() -> int:
    print(f"{len(CASES)} cases:")
    for case in CASES:
        print(f"  {case.name}")
        print(f"    asks    {case.question}")
        if case.about:
            print(f"    about   {case.about}")
        if case.needs:
            print(f"    needs   {', '.join(case.needs)}")
        for check in case.checks:
            print(f"    checks  {check.wants}")
    return 0
