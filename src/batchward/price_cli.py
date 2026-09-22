"""``batchward price-guard`` and ``batchward ceilings``: the Price Guard and its rules.

``price-guard`` runs against a simulated stockist. ``ceilings`` records notified
ceiling prices in Batchward's own records database (ADR 0010, ADR 0011), where
the agents' price tools read them, one at a time or a whole NPPA notification
table at once (ADR 0013).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from batchward.agents.data import DataUnavailableError, load_marg
from batchward.arguments import non_negative_int, positive_int, rupees
from batchward.bridge.marg_contract import MargLayoutError
from batchward.compliance.nppa import convert, read_notification
from batchward.compliance.prices import (
    Cause,
    CeilingPrice,
    Verdict,
    check_batch_price,
    overcharge_exposure,
    price_rises,
)
from batchward.core.clock import end_of_day
from batchward.records.store import RecordsError, RecordStore
from batchward.reporting.inr import format_inr
from batchward.sim.business import SimConfig, simulate
from batchward.sim.price_scenario import (
    REVISION_DAY,
    RISE_RECEIVED,
    RISE_SELLING_DAYS,
    seed_price_control,
)


def add_price_commands(commands: argparse._SubParsersAction) -> None:
    guard = commands.add_parser(
        "price-guard",
        help="check a simulated stockist's prices against dated ceiling prices and report exposure",
    )
    guard.add_argument("--start", type=date.fromisoformat, default=date(2025, 9, 1))
    guard.add_argument("--days", type=positive_int, default=365)
    guard.add_argument("--seed", type=int, default=42)
    guard.add_argument("--chemists", type=positive_int, default=380)
    guard.add_argument(
        "--top", type=non_negative_int, default=5, help="how many batches to list per section"
    )
    guard.set_defaults(handler=_price_guard)

    ceilings = commands.add_parser(
        "ceilings", help="record and list the ceiling prices bills are checked against"
    )
    actions = ceilings.add_subparsers(dest="ceilings_command", required=True)
    add = actions.add_parser("add", help="record a notified ceiling price from its date")
    add.add_argument("--records", type=Path, required=True, help="Batchward records database")
    add.add_argument("--molecule", required=True)
    add.add_argument("--strength", required=True)
    add.add_argument("--unit", required=True, help='unit sold, e.g. "strip of 10 tablets"')
    add.add_argument("--ceiling", type=rupees, required=True, help="per unit sold, without GST")
    add.add_argument("--effective", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
    add.add_argument("--reference", required=True, help="the notification it comes from")
    add.set_defaults(handler=_add_ceiling)
    table = actions.add_parser(
        "import",
        help="record every ceiling price in an NPPA notification table, per pack stocked",
    )
    table.add_argument("--records", type=Path, required=True, help="Batchward records database")
    table.add_argument("--marg", type=Path, required=True, help="Marg database of items stocked")
    table.add_argument("--csv", type=Path, required=True, help="the notification's table as CSV")
    table.add_argument(
        "--notification", required=True, help='the notification\'s number, e.g. "S.O. 1234(E)"'
    )
    table.add_argument(
        "--effective", type=date.fromisoformat, required=True, help="the date it takes effect"
    )
    table.add_argument(
        "--dry-run", action="store_true", help="show the conversion without recording anything"
    )
    table.set_defaults(handler=_import_ceilings)

    listing = actions.add_parser("list", help="list the ceiling prices recorded")
    listing.add_argument("--records", type=Path, required=True)
    listing.set_defaults(handler=_list_ceilings)


def _add_ceiling(args: argparse.Namespace) -> int:
    try:
        price = CeilingPrice(
            molecule=args.molecule,
            strength=args.strength,
            unit=args.unit,
            ceiling=args.ceiling,
            effective_from=args.effective,
            reference=args.reference,
        )
        with RecordStore(args.records) as store:
            added = store.save_ceiling(price)
    except (RecordsError, ValueError) as error:
        print(f"batchward ceilings: {error}", file=sys.stderr)
        return 1
    what = f"{price.molecule} {price.strength}, {price.unit}"
    if added:
        print(
            f"Recorded a ceiling of {format_inr(price.ceiling, paise=True)} without GST for "
            f"{what}, in force from {price.effective_from:%d/%m/%Y} under {price.reference}."
        )
    else:
        print(f"Already recorded: {what} from {price.effective_from:%d/%m/%Y}.")
    return 0


def _import_ceilings(args: argparse.Namespace) -> int:
    try:
        stock = load_marg(args.marg)
        with args.csv.open(encoding="utf-8-sig", newline="") as lines:
            notified = read_notification(lines)
        result = convert(
            notified,
            stock.items.values(),
            reference=args.notification,
            effective_from=args.effective,
        )
        added = 0
        if not args.dry_run:
            with RecordStore(args.records) as store, store.transaction():
                added = sum(store.save_ceiling(price) for price in result.prices)
    except (
        DataUnavailableError,
        MargLayoutError,
        RecordsError,
        OSError,
        ValueError,
        sqlite3.Error,
    ) as error:
        print(f"batchward ceilings: {error}", file=sys.stderr)
        return 1

    print(
        f"{args.notification}, in force from {args.effective:%d/%m/%Y}: "
        f"{len(notified)} formulations notified"
    )
    if args.dry_run:
        print(f"  Would record {len(result.prices)} ceiling prices for the packs stocked:")
    else:
        already = len(result.prices) - added
        note = f" ({already} already recorded)" if already else ""
        print(f"  Recorded {added} ceiling prices for the packs stocked{note}:")
    for price in result.prices:
        print(
            f"    {price.molecule} {price.strength}, {price.unit}: "
            f"{format_inr(price.ceiling, paise=True)} without GST"
        )
    if result.not_stocked:
        count = len(result.not_stocked)
        verb = "matches" if count == 1 else "match"
        print(f"  {_plural(count, 'notified formulation')} {verb} no item stocked")
    if result.unconverted:
        print("  Enter these by hand with `batchward ceilings add`:")
        packs: dict[tuple[str, int, str], list[str]] = {}
        for gap in result.unconverted:
            item = gap.item
            what = f"{item.molecule} {item.strength}, {item.unit}"
            packs.setdefault((what, gap.notified.row, gap.reason), []).append(item.brand)
        for (what, row, reason), brands in packs.items():
            print(f"    {what} ({_plural(len(brands), 'brand')}), line {row}: {reason}")
    if result.not_marked_scheduled:
        print("  Not marked as scheduled in Marg, so the Price Guard does not check them yet:")
        formulations: dict[str, list[str]] = {}
        for item in result.not_marked_scheduled:
            what = f"{item.molecule} {item.strength}, {item.unit}"
            formulations.setdefault(what, []).append(item.brand)
        for what, brands in formulations.items():
            print(f"    {what}: {', '.join(brands)}")
    return 0


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _list_ceilings(args: argparse.Namespace) -> int:
    try:
        with RecordStore(args.records, create=False) as store:
            prices = store.ceiling_prices()
    except RecordsError as error:
        print(f"batchward ceilings: {error}", file=sys.stderr)
        return 1
    if not prices:
        print("No ceiling prices recorded.")
    for price in prices:
        print(
            f"{price.effective_from:%d/%m/%Y}  {format_inr(price.ceiling, paise=True):>11}  "
            f"{price.molecule} {price.strength}, {price.unit}  ({price.reference})"
        )
    return 0


def _price_guard(args: argparse.Namespace) -> int:
    try:
        business = simulate(
            SimConfig(start=args.start, days=args.days, seed=args.seed, n_chemists=args.chemists)
        )
    except ValueError as error:
        print(f"batchward price-guard: {error}", file=sys.stderr)
        return 1
    try:
        scenario = seed_price_control(business)
    except (LookupError, ValueError) as error:
        print(
            f"batchward price-guard: seed {args.seed} cannot hold the price scenario ({error}); "
            "try another --seed",
            file=sys.stderr,
        )
        return 1
    items = {item.id: item for item in business.catalogue.items}
    today = args.start + timedelta(days=args.days)
    lowered = scenario.lowered

    print(
        f"Price Guard against a simulated stockist: {args.days} days from "
        f"{args.start.isoformat()}, seed {args.seed}"
    )
    print(f"  {len(scenario.ceilings)} ceiling prices on record, each in force from its own date")
    gst = items[next(iter(scenario.over_ceiling))].gst_rate
    print(
        f"  On {lowered.effective_from:%d/%m/%Y} {lowered.reference} lowered the ceiling for "
        f"{lowered.molecule} {lowered.strength}, {lowered.unit}, to "
        f"{format_inr(lowered.ceiling, paise=True)} "
        f"({format_inr(lowered.max_retail_price(gst), paise=True)} with GST)"
    )

    closing = end_of_day(today - timedelta(days=1))
    on_hand: dict = {}
    for (key, _location), units in business.ledger.balances(closing).items():
        on_hand[key] = on_hand.get(key, 0) + units
    checks = [
        check_batch_price(
            items[key.item_id], business.batches[key], on=today, ceilings=scenario.ceilings
        )
        for key in sorted(on_hand)
    ]
    blocked = [c for c in checks if c.verdict is Verdict.BLOCK]
    warned = [c for c in checks if c.verdict is Verdict.WARN]
    print()
    print(f"Before billing on {today:%d/%m/%Y}: batches on hand")
    allowed = len(checks) - len(blocked) - len(warned)
    print(f"  {len(blocked)} blocked, {len(warned)} to check by hand, {allowed} allowed")
    for check in blocked[: args.top]:
        item = items[check.batch.item_id]
        print(
            f"  BLOCK  {item.brand:14}{check.batch.batch_no:8}{on_hand[check.batch]:>6,} units  "
            f"MRP {format_inr(business.batches[check.batch].mrp, paise=True)} over "
            f"{format_inr(check.max_retail_price, paise=True)} ({check.ceiling.reference})"
        )

    exposure = overcharge_exposure(
        business.ledger, business.batches, items, scenario.ceilings, as_of=today
    )
    print()
    print(f"Overcharge exposure as of {today:%d/%m/%Y}, with 15% simple interest a year")
    for cause in Cause:
        lines = [o for o in exposure.overcharges if o.cause is cause]
        if not lines:
            continue
        amount = sum(o.amount for o in lines)
        interest = sum(o.interest for o in lines)
        print(
            f"  {cause}: {sum(o.units for o in lines):,} units on {len(lines):,} bill lines, "
            f"{format_inr(amount, paise=True)} plus {format_inr(interest, paise=True)} interest"
        )
    print(f"  {'Batch':34}{'Units':>7}{'Overcharge':>14}{'Interest':>12}")
    for key, (units, amount, interest) in list(exposure.by_batch().items())[: args.top]:
        label = f"{items[key.item_id].brand} {key.batch_no}"
        print(
            f"  {label:34}{units:>7,}{format_inr(amount, paise=True):>14}"
            f"{format_inr(interest, paise=True):>12}"
        )
    units = sum(o.units for o in exposure.overcharges)
    print(
        f"  {'Total':34}{units:>7,}{format_inr(exposure.amount, paise=True):>14}"
        f"{format_inr(exposure.interest, paise=True):>12}"
    )
    print(f"  Exposure including interest: {format_inr(exposure.total, paise=True)}")
    for rise in price_rises(business.batches.values(), items):
        print(
            f"  {items[rise.batch.item_id].brand} batch {rise.batch.batch_no}: MRP "
            f"{format_inr(rise.mrp, paise=True)} is {rise.rise:.1%} above "
            f"{format_inr(rise.reference_mrp, paise=True)} on batch "
            f"{rise.reference_batch.batch_no}, made within the year before; at most "
            f"{format_inr(rise.allowed_mrp, paise=True)} allowed"
        )
    if scenario.price_rise is None:
        sold_by = RISE_RECEIVED + timedelta(days=RISE_SELLING_DAYS)
        print(
            "  No price rise seeded: it needs the simulation to run from "
            f"{RISE_RECEIVED:%d/%m/%Y} to {sold_by:%d/%m/%Y}."
        )
    if today <= REVISION_DAY:
        print(
            f"  The simulation ends before {REVISION_DAY:%d/%m/%Y}, when the lowered ceiling "
            "takes effect; run longer to see it."
        )
    return 0
