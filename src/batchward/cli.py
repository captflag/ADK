"""Command-line entry points."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from batchward.bridge.marg_export import export_to_marg
from batchward.sim.business import SimConfig, simulate
from batchward.sim.scenarios import seed_recall

_RECALL_RECEIVED = date(2026, 1, 5)
_RECALL_NOTICE = date(2026, 2, 12)
_RECALL_CHEMISTS = 38


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="batchward")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser(
        "demo-marg",
        help="simulate a pharma stockist and save it as a mock Marg database",
    )
    demo.add_argument("output", type=Path, help="where to write the SQLite database")
    demo.add_argument("--start", type=date.fromisoformat, default=date(2025, 9, 1))
    demo.add_argument("--days", type=int, default=180)
    demo.add_argument("--seed", type=int, default=42)
    demo.add_argument("--chemists", type=int, default=380)
    demo.add_argument("--force", action="store_true", help="overwrite an existing file")

    args = parser.parse_args(argv)
    return _demo_marg(args)


def _demo_marg(args: argparse.Namespace) -> int:
    output: Path = args.output
    if output.exists() and not args.force:
        print(f"{output} already exists; pass --force to overwrite it.", file=sys.stderr)
        return 1

    business = simulate(
        SimConfig(start=args.start, days=args.days, seed=args.seed, n_chemists=args.chemists)
    )
    end = args.start + timedelta(days=args.days - 1)
    covers_recall = args.start <= _RECALL_RECEIVED and end >= _RECALL_NOTICE
    recall = None
    if covers_recall and args.chemists >= _RECALL_CHEMISTS:
        recall = seed_recall(business)

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial")
    partial.unlink(missing_ok=True)
    with closing(sqlite3.connect(partial)) as connection:
        export_to_marg(
            connection,
            parties=[*business.catalogue.companies, *business.chemists],
            items=business.catalogue.items,
            batches=business.batches,
            ledger=business.ledger,
        )
    partial.replace(output)

    print(f"Wrote {output}")
    print(
        f"  {args.days} days from {args.start.isoformat()}: {len(business.catalogue.items)} items, "
        f"{len(business.chemists)} chemists"
    )
    print(f"  {len(business.ledger):,} movements across {len(business.batches):,} batches")
    if recall is None:
        print(
            "  No recall seeded: it needs the simulation to run from "
            f"{_RECALL_RECEIVED.isoformat()} to {_RECALL_NOTICE.isoformat()} "
            f"with at least {_RECALL_CHEMISTS} chemists."
        )
    else:
        print(
            f"  Seeded Class {recall.recall_class} recall: batch {recall.batch.batch_no}, "
            f"supplied to {len(recall.chemists)} chemists, {recall.units_on_hand} strips on hand"
        )
    return 0
