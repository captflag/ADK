"""Command-line entry points."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from batchward.analysis.demand import daily_sales
from batchward.analysis.evaluation import evaluate, naive_last_period
from batchward.analysis.forecast import croston_sba, exponential_smoothing, moving_average, tsb
from batchward.analysis.health import stock_health
from batchward.approvals_cli import add_approval_commands
from batchward.arguments import non_negative_int, positive_int
from batchward.bridge.marg_export import export_to_marg
from batchward.bridge.marg_layout import format_expiry
from batchward.brief_cli import add_brief_commands
from batchward.buying.cases import CaseSize
from batchward.claims_cli import add_claims_commands
from batchward.cover_cli import add_cover_commands
from batchward.intake_cli import add_intake_commands
from batchward.orders_cli import add_orders_commands
from batchward.price_cli import add_price_commands
from batchward.recall_cli import add_recall_commands
from batchward.records.recalls import receive_notice
from batchward.records.store import RecordsError, RecordStore
from batchward.registrar_cli import add_registrar_commands
from batchward.reporting.inr import format_inr
from batchward.reporting.recall import format_moment
from batchward.sim.business import SimConfig, simulate
from batchward.sim.invoices import count_sheet, purchase_invoices, render_text
from batchward.sim.price_scenario import RISE_RECEIVED, RISE_SELLING_DAYS, seed_price_control
from batchward.sim.recall_drill import run_recall_drill
from batchward.sim.return_terms import return_terms
from batchward.sim.scenarios import recall_notice, seed_recall
from batchward.whatsapp_cli import add_whatsapp_commands

_RECALL_RECEIVED = date(2026, 1, 5)
_RECALL_NOTICE = date(2026, 2, 12)
_RECALL_CHEMISTS = 38


def main(argv: list[str] | None = None) -> int:
    _print_utf8()
    parser = argparse.ArgumentParser(prog="batchward")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser(
        "demo-marg",
        help="simulate a pharma stockist and save it as a mock Marg database",
    )
    demo.add_argument("output", type=Path, help="where to write the SQLite database")
    demo.add_argument("--start", type=date.fromisoformat, default=date(2025, 9, 1))
    demo.add_argument("--days", type=positive_int, default=365)
    demo.add_argument("--seed", type=int, default=42)
    demo.add_argument("--chemists", type=positive_int, default=380)
    demo.add_argument(
        "--records",
        type=Path,
        help=(
            "also write a Batchward records database: the recall notice received, ceilings, "
            "and the companies' return terms"
        ),
    )
    demo.add_argument(
        "--invoices",
        type=Path,
        help="also write the last 30 days' supplier invoices as text and JSON, for `intake`",
    )
    demo.add_argument("--force", action="store_true", help="overwrite existing files")
    demo.set_defaults(handler=_demo_marg)

    backtest = commands.add_parser(
        "backtest",
        help="score forecasting methods on simulated sales, grouped by demand pattern",
    )
    backtest.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 2))
    backtest.add_argument("--days", type=positive_int, default=728)
    backtest.add_argument("--seed", type=int, default=42)
    backtest.add_argument(
        "--min-history", type=positive_int, default=26, help="weeks before forecasting"
    )
    backtest.set_defaults(handler=_backtest)

    health = commands.add_parser(
        "health",
        help="report stock value, ageing, dead stock and expiry risk for a simulated stockist",
    )
    health.add_argument("--start", type=date.fromisoformat, default=date(2025, 9, 1))
    health.add_argument("--days", type=positive_int, default=365)
    health.add_argument("--seed", type=int, default=42)
    health.add_argument(
        "--top", type=non_negative_int, default=5, help="how many items to list per section"
    )
    health.set_defaults(handler=_health)

    drill = commands.add_parser(
        "recall-drill",
        help="run the Class I recall drill against a simulated stockist and print the report",
    )
    drill.add_argument("--start", type=date.fromisoformat, default=date(2025, 9, 1))
    drill.add_argument("--days", type=positive_int, default=180)
    drill.add_argument("--seed", type=int, default=42)
    drill.add_argument("--chemists", type=positive_int, default=380)
    drill.set_defaults(handler=_recall_drill)

    add_recall_commands(commands)
    add_price_commands(commands)
    add_registrar_commands(commands)
    add_intake_commands(commands)
    add_approval_commands(commands)
    add_claims_commands(commands)
    add_orders_commands(commands)
    add_whatsapp_commands(commands)
    add_brief_commands(commands)
    add_cover_commands(commands)

    args = parser.parse_args(argv)
    return args.handler(args)


def _print_utf8() -> None:
    """Print rupee signs whatever the output is.

    On Windows, output sent to a pipe or a file is encoded in the ANSI code page,
    which has no rupee sign, so a report saved with ``>`` would stop at the first
    amount. Such output is switched to UTF-8; a console is left as it is.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _demo_marg(args: argparse.Namespace) -> int:
    output: Path = args.output
    records: Path | None = args.records
    for path in (output, records):
        if path is not None and path.exists() and not args.force:
            print(f"{path} already exists; pass --force to overwrite it.", file=sys.stderr)
            return 1

    try:
        business = simulate(
            SimConfig(start=args.start, days=args.days, seed=args.seed, n_chemists=args.chemists)
        )
    except ValueError as error:
        print(f"batchward demo-marg: {error}", file=sys.stderr)
        return 1
    end = args.start + timedelta(days=args.days - 1)
    covers_recall = args.start <= _RECALL_RECEIVED and end >= _RECALL_NOTICE
    recall = None
    if covers_recall and args.chemists >= _RECALL_CHEMISTS:
        recall = seed_recall(business)
    try:
        prices, price_note = seed_price_control(business), ""
    except (LookupError, ValueError) as error:
        prices, price_note = None, str(error)

    partial = output.with_name(output.name + ".partial")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
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
    except (OSError, sqlite3.Error, ValueError) as error:
        partial.unlink(missing_ok=True)
        print(f"batchward demo-marg: cannot write {output}: {error}", file=sys.stderr)
        return 1

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
    if prices is None:
        print(f"  No price problems seeded: {price_note}; another --seed may hold them.")
    else:
        lowered = prices.lowered
        print(
            f"  Seeded price problems: the {lowered.molecule} {lowered.strength} ceiling is "
            f"lowered on {lowered.effective_from:%d/%m/%Y} below {len(prices.over_ceiling)} brands"
        )
        if prices.price_rise is None:
            print(
                "  No price rise seeded: it needs the simulation to run from "
                f"{RISE_RECEIVED.isoformat()} to "
                f"{(RISE_RECEIVED + timedelta(days=RISE_SELLING_DAYS)).isoformat()}."
            )
        else:
            print(
                f"  Seeded price rise: batch {prices.price_rise.batch_no} is priced 18% above "
                "earlier stock"
            )

    if args.invoices is not None and not _write_invoices(business, args, end):
        return 1

    if records is not None:
        try:
            records.parent.mkdir(parents=True, exist_ok=True)
            records.unlink(missing_ok=True)
            store = RecordStore(records)
        except (OSError, RecordsError) as error:
            print(f"batchward demo-marg: cannot write {records}: {error}", file=sys.stderr)
            return 1
        with store:
            print(f"Wrote {records}")
            if recall is not None:
                notice = recall_notice(business, recall)
                received = receive_notice(
                    store,
                    notice,
                    ledger=business.ledger,
                    items={item.id: item for item in business.catalogue.items},
                    parties={p.id: p for p in (*business.catalogue.companies, *business.chemists)},
                    # The simulated stockist acts the moment the notice arrives.
                    at=notice.received_at,
                    batches=business.batches,
                )
                print(
                    f"  Received notice {notice.reference} on {format_moment(notice.received_at)} "
                    f"and blocked {len(received.holds)} "
                    f"{'batch' if len(received.holds) == 1 else 'batches'} automatically"
                )
            if prices is not None:
                with store.transaction():
                    saved = sum(store.save_ceiling(price) for price in prices.ceilings)
                print(f"  Recorded {saved} ceiling prices")
            # Terms in force well before the first day, so every batch has its window.
            terms = return_terms(
                business.catalogue, effective_from=args.start - timedelta(days=730)
            )
            with store.transaction():
                saved = sum(store.save_terms(entry) for entry in terms)
            print(f"  Recorded return terms for {saved} companies")
            with store.transaction():
                saved = sum(
                    store.save_case_size(CaseSize(item_id, units, args.start, "Simulated packing"))
                    for item_id, units in sorted(business.case_sizes.items())
                )
            print(f"  Recorded case sizes for {saved} products")
    return 0


def _write_invoices(business, args: argparse.Namespace, end: date) -> bool:
    start = max(args.start, end - timedelta(days=29))
    invoices = purchase_invoices(business, start=start, end=end)
    # Each invoice as printed, for `intake extract` to read; its reading as JSON, the ground
    # truth a correct extraction reproduces; the godown's count; and the order it fills.
    orders = {order.number: order for order in business.orders}
    files = {}
    for invoice in invoices:
        name = re.sub(r"[^A-Za-z0-9]+", "-", invoice.invoice_no)
        files[args.invoices / f"{name}.txt"] = render_text(invoice)
        files[args.invoices / f"{name}.json"] = invoice.model_dump_json(indent=2) + "\n"
        files[args.invoices / f"{name}.count.csv"] = count_sheet(business, invoice)
        if invoice.order_no in orders:
            order = orders[invoice.order_no]
            order_name = re.sub(r"[^A-Za-z0-9]+", "-", order.number)
            files[args.invoices / f"{order_name}.order.json"] = order.to_json() + "\n"
    if not args.force and any(path.exists() for path in files):
        print(f"{args.invoices} already holds these invoices; pass --force.", file=sys.stderr)
        return False
    try:
        args.invoices.mkdir(parents=True, exist_ok=True)
        for path, text in files.items():
            path.write_text(text, encoding="utf-8")
    except OSError as error:
        print(f"batchward demo-marg: cannot write invoices: {error}", file=sys.stderr)
        return False
    print(
        f"Wrote {len(invoices)} supplier invoices from {start:%d/%m/%Y} to {end:%d/%m/%Y} "
        f"to {args.invoices}: each as printed (.txt), as read (.json) and as counted "
        "(.count.csv), with the orders they fill (.order.json)"
    )
    return True


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


def _health(args: argparse.Namespace) -> int:
    business = simulate(SimConfig(start=args.start, days=args.days, seed=args.seed))
    on = args.start + timedelta(days=args.days)
    report = stock_health(business.ledger, business.locations, on=on)
    items = {item.id: item for item in business.catalogue.items}

    print(f"Stock health on {on.day} {on:%B %Y}, simulated stockist (seed {args.seed})")
    print()
    print(f"{'Stock at cost':24}{format_inr(report.stock_value):>16}")
    for bucket, value in report.value_by_age.items():
        print(f"  {bucket:22}{format_inr(value):>16}")
    if report.unvalued_units:
        print(f"  {report.unvalued_units:,} units have no known cost and are not valued")

    print()
    print(
        f"{'Dead stock':24}{format_inr(report.dead_stock_value):>16}"
        f"  across {len(report.dead_stock)} items with no sale in 120 days"
    )
    for dead in report.dead_stock[: args.top]:
        print(
            f"  {items[dead.item_id].brand:22}{dead.units:>7,} units"
            f"{format_inr(dead.value):>13}  idle {dead.idle_days} days"
        )

    print()
    print(
        f"{'Expiry risk':24}{format_inr(report.expiry_value_at_risk):>16}"
        f"  across {len(report.expiry_risks)} batches expiring within 180 days"
    )
    for risk in report.expiry_risks[: args.top]:
        batch = risk.batch
        label = f"{items[batch.item_id].brand} {batch.batch_no} exp {format_expiry(batch.expiry)}"
        value = "cost unknown" if risk.value_at_risk is None else format_inr(risk.value_at_risk)
        print(f"  {label:34}{risk.units_at_risk:>6,} of {risk.units:<6,}{value:>11}  {risk.reason}")

    print()
    print("Consumption at cost, last 365 days")
    for value_class, value in report.consumption_by_class.items():
        count = report.items_by_class[value_class]
        print(f"  {value_class}  {count:>4} items{format_inr(value):>17}")
    return 0


def _recall_drill(args: argparse.Namespace) -> int:
    if args.chemists < _RECALL_CHEMISTS:
        print(f"The drill needs at least {_RECALL_CHEMISTS} chemists.", file=sys.stderr)
        return 1
    try:
        business = simulate(
            SimConfig(start=args.start, days=args.days, seed=args.seed, n_chemists=args.chemists)
        )
    except ValueError as error:
        print(f"batchward recall-drill: {error}", file=sys.stderr)
        return 1
    history = len(business.ledger)
    result = run_recall_drill(business)

    print(
        f"Recall drill against a simulated stockist: {args.days} days from "
        f"{args.start.isoformat()}, seed {args.seed}, {history:,} movements searched"
    )
    print()
    print(result.report_text)
    print()
    items = {item.id: item for item in business.catalogue.items}
    companies = {company.id: company for company in business.catalogue.companies}
    print("Batches raised for review, not blocked")
    for candidate in result.recall.match.review:
        batch = candidate.batch
        print(
            f"  {batch.batch_no}, expiry {format_expiry(batch.expiry)}, "
            f"{items[batch.item_id].brand} from {companies[batch.company_id].name}"
        )
        for reason in candidate.reasons:
            print(f"    {reason}")
    print()
    print("Drill checks")
    for check in result.checks:
        print(f"  {'PASS' if check.passed else 'FAIL'}  {check.name}: {check.detail}")
    print()
    print("Drill passed." if result.passed else "Drill FAILED.")
    return 0 if result.passed else 1
