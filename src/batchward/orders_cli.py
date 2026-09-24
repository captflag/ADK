"""``batchward orders``: what to order, and purchase orders placed on approval (ADR 0020).

``orders suggest`` shows, company by company, what needs ordering to cover the
forecast, each item to the cover of its ABC-XYZ class (ADR 0022) unless
``--cover-days`` gives one cover for every item. Orders are rounded up to whole
cases where the case size is on record (ADR 0023). ``orders draft`` drafts an
order on one company and puts it up for approval (ADR 0005); only an approval
places it.
``orders list`` shows orders placed and what each still waits for, and ``orders
cases`` records and lists how many units each product's case holds.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from batchward.agents import ordering
from batchward.agents.data import DataUnavailableError
from batchward.approvals_cli import request_approval
from batchward.arguments import non_negative_int, positive_int
from batchward.bridge.marg_contract import MargLayoutError
from batchward.buying.cases import SLOW_CASE_DAYS, CaseSizesFileError, read_case_sizes
from batchward.buying.order import OPEN_DAYS
from batchward.buying.planning import open_orders, plan_for
from batchward.buying.suggest import LEAD_DAYS, Policy
from batchward.records.store import RecordsError, RecordStore
from batchward.reporting.inr import format_inr
from batchward.whatsapp_cli import notifier

_FAILURES = (
    DataUnavailableError,
    MargLayoutError,
    RecordsError,
    OSError,
    ValueError,
    sqlite3.Error,
)


def add_orders_commands(commands: argparse._SubParsersAction) -> None:
    orders = commands.add_parser("orders", help="what to order, and purchase orders placed")
    actions = orders.add_subparsers(dest="orders_command", required=True)

    suggest = actions.add_parser("suggest", help="what needs ordering, company by company")
    _plan(suggest)
    suggest.add_argument("--company", help="only this company's items")
    suggest.add_argument("--limit", type=int, default=20, help="items to list (default 20)")
    suggest.set_defaults(handler=_suggest)

    draft = actions.add_parser("draft", help="draft an order on one company and ask for approval")
    draft.add_argument("company", help="the company's code in Marg, e.g. C06")
    _plan(draft)
    draft.add_argument("--out", type=Path, required=True, help="folder for the order's files")
    draft.add_argument("--approve-by", help="approve it at once, as this person")
    draft.add_argument(
        "--notify", action="store_true", help="send the request to the approvers on WhatsApp"
    )
    draft.add_argument("--approvers", type=Path, help="CSV of phone,name to notify")
    draft.set_defaults(handler=_draft)

    listing = actions.add_parser("list", help="orders placed, and what each still waits for")
    listing.add_argument("--records", type=Path, required=True, help="Batchward records database")
    listing.add_argument("--all", action="store_true", help="orders received in full too")
    listing.add_argument("--on", type=date.fromisoformat, help="the day to age orders to")
    listing.set_defaults(handler=_list)

    cases = actions.add_parser("cases", help="how many units each product's case holds")
    case_actions = cases.add_subparsers(dest="cases_command", required=True)
    load = case_actions.add_parser("import", help="record case sizes from a CSV file")
    load.add_argument("file", type=Path, help="CSV: product code, units per case, from, ref")
    load.add_argument("--records", type=Path, required=True, help="Batchward records database")
    load.set_defaults(handler=_import_cases)
    shown = case_actions.add_parser("list", help="the case sizes recorded")
    shown.add_argument("--records", type=Path, required=True, help="Batchward records database")
    shown.set_defaults(handler=_list_cases)


def _plan(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--marg", type=Path, required=True, help="Marg database")
    parser.add_argument("--records", type=Path, required=True, help="Batchward records database")
    parser.add_argument(
        "--cover-days",
        type=positive_int,
        help="one cover for every item, in days of demand (default: each item's by its class)",
    )
    parser.add_argument(
        "--lead-days",
        type=non_negative_int,
        default=LEAD_DAYS,
        help=f"days for stock to arrive ({LEAD_DAYS})",
    )


def _policy(args: argparse.Namespace) -> Policy:
    return Policy(cover_days=args.cover_days, lead_days=args.lead_days)


def _suggest(args: argparse.Namespace) -> int:
    policy = _policy(args)
    try:
        planned = plan_for(args.marg, args.records, policy=policy)
    except _FAILURES as error:
        return _fail(error)
    stock = planned.stock
    wanted = [
        s
        for s in planned.suggestions
        if s.quantity > 0 and (args.company is None or s.item.company_id == args.company)
    ]
    by_company: defaultdict[str, list] = defaultdict(list)
    for suggestion in wanted:
        by_company[suggestion.item.company_id].append(suggestion)
    total = sum((s.value or Decimal(0) for s in wanted), Decimal(0))
    print(
        f"To order on {stock.today:%d/%m/%Y}, covering {policy}: {len(wanted)} items from "
        f"{len(by_company)} {'company' if len(by_company) == 1 else 'companies'}, about "
        f"{format_inr(total, paise=True)} at last purchase rates."
    )
    for company_id, items in sorted(
        by_company.items(), key=lambda entry: -sum(s.value or 0 for s in entry[1])
    ):
        value = sum((s.value or Decimal(0) for s in items), Decimal(0))
        name = stock.parties[company_id].name if company_id in stock.parties else company_id
        noun = "item" if len(items) == 1 else "items"
        print(f"  {company_id:<6}{name[:28]:<29}{len(items):>4} {noun:<5}{format_inr(value):>14}")
    if planned.overdue:
        print(f"\nOrders more than {OPEN_DAYS} days old with units never delivered:")
        for open_order in planned.overdue:
            units = sum(open_order.due().values())
            print(
                f"  {open_order.order.number}  {units} units due, "
                f"{open_order.age(stock.today)} days old"
            )
    shown = wanted[: max(1, args.limit)]
    if shown:
        print(
            f"\n  {'Company':<9}{'Product':<22}{'Class':<6}{'Cover':>14}{'Per day':>8}"
            f"{'Usable':>8}{'Due':>6}{'Order':>7}{'Cases':>10}{'Value':>14}"
        )
        for s in shown:
            cases = "" if s.case_units is None else f"{s.cases} x {s.case_units}"
            print(
                f"  {s.item.company_id:<9}{s.item.brand[:21]:<22}{s.item_class or ''!s:<6}"
                f"{s.cover!s:>14}{s.daily_rate:>8.1f}{s.usable:>8}{s.due:>6}{s.quantity:>7}"
                f"{cases:>10}{format_inr(s.value or Decimal(0), paise=True):>14}"
            )
    slow = [s for s in wanted if s.slow_case]
    if slow:
        print(
            f"\nOne case holds more than {SLOW_CASE_DAYS} days' demand of these; check the "
            "stock will sell before it expires:"
        )
        for s in slow:
            print(f"  {s.item.company_id:<9}{s.item.brand[:21]:<22}{s.case_days:>6.0f} days a case")
    return 0


def _draft(args: argparse.Namespace) -> int:
    policy = _policy(args)
    try:
        planned = plan_for(args.marg, args.records, policy=policy, company_id=args.company)
    except _FAILURES as error:
        return _fail(error)
    company = planned.stock.parties[args.company]
    if planned.order is None or planned.posting is None:
        print(f"Nothing needs ordering from {company.name} today.")
        return 0
    by_item = {s.item.id: s for s in planned.suggestions}
    print(f"Order {planned.order.number} on {company.name}:")
    for line in planned.order.lines:
        suggestion = by_item[line.item_id]
        print(
            f"  {suggestion.item.brand[:21]:<22}{line.quantity:>7}"
            f"{format_inr(suggestion.value or Decimal(0), paise=True):>14}"
        )
    posting = planned.posting
    return request_approval(
        posting,
        records=args.records,
        start=lambda: ordering.ask_to_order(
            args.marg, args.records, args.company, policy, posting, out=args.out
        ),
        approve_by=args.approve_by,
        notify=notifier(args),
    )


def _list(args: argparse.Namespace) -> int:
    try:
        with RecordStore(args.records, create=False) as store:
            placed = open_orders(store)
    except _FAILURES as error:
        return _fail(error)
    on = args.on or date.today()
    rows = [o for o in placed if args.all or o.due()]
    if not rows:
        print("No orders are waiting for delivery." if not args.all else "No orders are recorded.")
        return 0
    print(f"  {'Order':<17}{'Company':<9}{'Placed':<11}{'Age':>5}{'Ordered':>9}{'Due':>7}")
    for open_order in rows:
        order = open_order.order
        ordered = sum(line.quantity for line in order.lines)
        due = sum(open_order.due().values())
        late = "  overdue" if due and open_order.age(on) > OPEN_DAYS else ""
        print(
            f"  {order.number:<17}{order.company_id:<9}{order.placed_on:%d/%m/%Y} "
            f"{open_order.age(on):>5}{ordered:>9}{due:>7}{late}"
        )
    return 0


def _import_cases(args: argparse.Namespace) -> int:
    try:
        with args.file.open(encoding="utf-8-sig", newline="") as lines:
            sizes = read_case_sizes(lines)
        with RecordStore(args.records) as store, store.transaction():
            saved = sum(store.save_case_size(size) for size in sizes)
    except (CaseSizesFileError, *_FAILURES) as error:
        return _fail(error)
    print(f"Recorded {saved} of {len(sizes)} case sizes; {len(sizes) - saved} already recorded.")
    return 0


def _list_cases(args: argparse.Namespace) -> int:
    try:
        with RecordStore(args.records, create=False) as store:
            sizes = store.case_sizes()
    except _FAILURES as error:
        return _fail(error)
    if not sizes:
        print("No case sizes are recorded; orders are in units. Import them with `orders cases`.")
        return 0
    print(f"  {'Product':<12}{'Units':>6}  From        Reference")
    for size in sizes:
        print(
            f"  {size.item_id:<12}{size.units:>6}  {size.effective_from:%d/%m/%Y}  {size.reference}"
        )
    return 0


def _fail(error: object) -> int:
    print(f"batchward orders: {error}", file=sys.stderr)
    return 1
