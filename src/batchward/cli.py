"""Command-line entry points."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from batchward.analysis.demand import daily_sales
from batchward.analysis.evaluation import evaluate, naive_last_period
from batchward.analysis.forecast import croston_sba, exponential_smoothing, moving_average, tsb
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
    demo.set_defaults(handler=_demo_marg)

    backtest = commands.add_parser(
        "backtest",
        help="score forecasting methods on simulated sales, grouped by demand pattern",
    )
    backtest.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 2))
    backtest.add_argument("--days", type=int, default=728)
    backtest.add_argument("--seed", type=int, default=42)
    backtest.add_argument("--min-history", type=int, default=26, help="weeks before forecasting")
    backtest.set_defaults(handler=_backtest)

    args = parser.parse_args(argv)
    return args.handler(args)


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


_NAIVE = "naive (last week)"


def _backtest(args: argparse.Namespace) -> int:
    weeks = args.days // 7
    if weeks <= args.min_history:
        print(
            f"{args.days} days give {weeks} whole weeks; at least {args.min_history + 1} "
            f"are needed to forecast after {args.min_history} weeks of history.",
            file=sys.stderr,
        )
        return 1

    business = simulate(SimConfig(start=args.start, days=args.days, seed=args.seed))
    daily = daily_sales(
        business.ledger, start=args.start, end=args.start + timedelta(days=args.days - 1)
    )
    methods = {
        _NAIVE: naive_last_period,
        "moving average (8 weeks)": moving_average,
        "exponential smoothing": exponential_smoothing,
        "Croston (SBA)": croston_sba,
        "TSB": tsb,
    }
    reports = evaluate(daily, methods, period=7, min_history=args.min_history)

    print(
        f"Backtest: {weeks} weeks from {args.start.isoformat()}, seed {args.seed}; "
        f"forecasting one week ahead after at least {args.min_history} weeks of history."
    )
    print(
        "Lower MASE is better; compare each method with the naive benchmark row. "
        "Positive bias means over-forecasting, which leaves excess stock."
    )
    for report in reports:
        naive = next(s for s in report.scores if s.method == _NAIVE)
        print(f"\n{report.pattern}: {report.items} items")
        print(f"  {'method':28} {'median MASE':>11} {'bias/week':>10}")
        for score in report.scores:
            mase = "n/a" if score.median_mase is None else f"{score.median_mase:.3f}"
            if score.method == _NAIVE:
                note = "  (benchmark)"
            elif (
                score.median_mase is not None
                and naive.median_mase is not None
                and score.median_mase < naive.median_mase
            ):
                note = "  beats benchmark"
            else:
                note = ""
            print(f"  {score.method:28} {mase:>11} {score.mean_bias:>+10.2f}{note}")
    return 0
