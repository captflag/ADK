"""``batchward claims``: return terms, claim windows, and claims made (ADR 0018, 0025).

``claims terms import`` records each company's return terms. ``claims windows``
shows what can be claimed now, which windows close soon, and what was written
off while it could still have been claimed. ``claims draft`` drafts a claim on
one company and puts it up for approval (ADR 0005); only an approval makes it.
``claims returns mark`` records why stock came back on a credit note, and
``claims breakage`` lists and claims what chemists sent back broken.
``claims list`` shows claims made by company and age, and ``claims settle``
records a company's credit note against one.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from batchward.agents import breakage as breakage_flow
from batchward.agents import claiming
from batchward.agents.data import DataUnavailableError, load_marg
from batchward.analysis.costs import batch_costs
from batchward.approvals_cli import request_approval
from batchward.bridge.marg_contract import MargLayoutError
from batchward.bridge.marg_layout import format_expiry
from batchward.claims.breakage import Reason, ReturnReason, normalise
from batchward.claims.claim import Settlement, settled
from batchward.claims.submission import draft_breakage_for, draft_for
from batchward.claims.terms import TermsFileError, read_terms
from batchward.claims.windows import CLOSING_DAYS, WindowState, lost_claims
from batchward.core.clock import IST, end_of_day
from batchward.core.models import MovementType
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
LOST_DAYS = 90
"""How far back ``claims windows`` looks for stock written off while claimable."""
OPENING_DAYS = 30
"""How far ahead ``claims windows`` looks for windows about to open."""


def add_claims_commands(commands: argparse._SubParsersAction) -> None:
    claims = commands.add_parser("claims", help="return terms, claim windows and expiry claims")
    actions = claims.add_subparsers(dest="claims_command", required=True)

    terms = actions.add_parser("terms", help="each company's terms for taking back expiring stock")
    term_actions = terms.add_subparsers(dest="terms_command", required=True)
    load = term_actions.add_parser("import", help="record return terms from a CSV file")
    load.add_argument("file", type=Path, help="CSV: company, opens, closes, credit %%, from, ref")
    _records(load)
    load.set_defaults(handler=_import_terms)
    listing = term_actions.add_parser("list", help="the return terms recorded")
    _records(listing)
    listing.set_defaults(handler=_list_terms)

    windows = actions.add_parser("windows", help="what can be claimed now, and what was lost")
    _stock(windows)
    windows.add_argument("--company", help="only this company's batches")
    windows.add_argument("--limit", type=int, default=20, help="batches to list (default 20)")
    windows.set_defaults(handler=_windows)

    draft = actions.add_parser("draft", help="draft a claim on one company and ask for approval")
    draft.add_argument("company", help="the company's code in Marg, e.g. C06")
    _stock(draft)
    draft.add_argument("--out", type=Path, required=True, help="folder for the claim's files")
    draft.add_argument("--approve-by", help="approve it at once, as this person")
    draft.add_argument(
        "--notify",
        action="store_true",
        help="send the request to the approvers on WhatsApp (see `batchward whatsapp`)",
    )
    draft.add_argument(
        "--approvers", type=Path, help="CSV of phone,name to notify (default BATCHWARD_APPROVERS)"
    )
    draft.set_defaults(handler=_draft)

    returns = actions.add_parser("returns", help="why stock came back from a chemist")
    return_actions = returns.add_subparsers(dest="returns_command", required=True)
    mark = return_actions.add_parser("mark", help="record why one credit note's stock came back")
    mark.add_argument("document", help="the credit note the return came back on")
    mark.add_argument(
        "--reason",
        type=Reason,
        choices=list(Reason),
        required=True,
        help="breakage, expiry, or other",
    )
    mark.add_argument("--by", required=True, help="who is recording it")
    mark.add_argument("--note", default="", help="anything worth keeping with it")
    _records(mark)
    mark.set_defaults(handler=_mark_return)
    marked = return_actions.add_parser("list", help="returns recorded, and with --marg those not")
    _records(marked)
    marked.add_argument("--marg", type=Path, help="Marg database, to list returns not yet marked")
    marked.add_argument("--limit", type=int, default=20, help="returns to list (default 20)")
    marked.set_defaults(handler=_list_returns)

    broken = actions.add_parser(
        "breakage", help="what chemists sent back broken, and claims for it"
    )
    broken_actions = broken.add_subparsers(dest="breakage_command", required=True)
    shown = broken_actions.add_parser("list", help="breakage in stock, by company")
    _stock(shown)
    shown.add_argument("--limit", type=int, default=20, help="batches to list (default 20)")
    shown.set_defaults(handler=_list_breakage)
    draft_breakage = broken_actions.add_parser(
        "draft", help="draft a breakage claim on one company and ask for approval"
    )
    draft_breakage.add_argument("company", help="the company's code in Marg, e.g. C06")
    _stock(draft_breakage)
    draft_breakage.add_argument(
        "--out", type=Path, required=True, help="folder for the claim's files"
    )
    draft_breakage.add_argument("--approve-by", help="approve it at once, as this person")
    draft_breakage.add_argument(
        "--notify", action="store_true", help="send the request to the approvers on WhatsApp"
    )
    draft_breakage.add_argument("--approvers", type=Path, help="CSV of phone,name to notify")
    draft_breakage.set_defaults(handler=_draft_breakage)

    made = actions.add_parser("list", help="claims made, by company and age")
    _records(made)
    made.add_argument("--all", action="store_true", help="settled claims too")
    made.add_argument("--on", type=date.fromisoformat, help="the day to age claims to")
    made.set_defaults(handler=_list_claims)

    settle = actions.add_parser("settle", help="record a company's credit note against a claim")
    settle.add_argument("claim", help="the claim number, e.g. CL/C06/260130")
    _records(settle)
    settle.add_argument("--credit-note", required=True, help="the company's credit note number")
    settle.add_argument("--amount", required=True, help="the credit note's amount in rupees")
    settle.add_argument(
        "--received", type=date.fromisoformat, required=True, help="the day it arrived"
    )
    settle.add_argument("--by", required=True, help="who is recording it")
    settle.set_defaults(handler=_settle)


def _records(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--records", type=Path, required=True, help="Batchward records database")


def _stock(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--marg", type=Path, required=True, help="Marg database")
    _records(parser)


def _import_terms(args: argparse.Namespace) -> int:
    try:
        with args.file.open(encoding="utf-8-sig", newline="") as lines:
            terms = read_terms(lines)
        with RecordStore(args.records) as store, store.transaction():
            saved = sum(store.save_terms(entry) for entry in terms)
    except (TermsFileError, *_FAILURES) as error:
        return _fail(error)
    print(f"Recorded {saved} of {len(terms)} return terms; {len(terms) - saved} already recorded.")
    return 0


def _list_terms(args: argparse.Namespace) -> int:
    try:
        with RecordStore(args.records, create=False) as store:
            terms = store.return_terms()
    except _FAILURES as error:
        return _fail(error)
    if not terms:
        print("No return terms are recorded. Import them with `claims terms import`.")
        return 0
    print(
        f"  {'Company':<9}{'Opens before':>13}{'Closes after':>13}{'Credit':>8}  "
        "From        Reference"
    )
    for entry in terms:
        print(
            f"  {entry.company_id:<9}{entry.opens_days_before_expiry:>8} days"
            f"{entry.closes_days_after_expiry:>8} days{entry.credit_percent.normalize():>7f}%  "
            f"{entry.effective_from:%d/%m/%Y}  {entry.reference}"
        )
    return 0


def _windows(args: argparse.Namespace) -> int:
    try:
        drafted = draft_for(args.marg, args.records)
        with RecordStore(args.records, create=False) as store:
            terms = store.terms_table()
    except _FAILURES as error:
        return _fail(error)
    stock, on = drafted.stock, drafted.stock.today
    windows = [w for w in drafted.windows if args.company is None or w.company_id == args.company]
    lost = [
        claim
        for claim in lost_claims(
            stock.ledger,
            batch_costs(stock.ledger),
            terms,
            since=end_of_day(on - timedelta(days=LOST_DAYS + 1)),
            until=end_of_day(on - timedelta(days=1)),
        )
        if args.company is None or claim.batch.company_id == args.company
    ]
    by_state: defaultdict[WindowState, list] = defaultdict(list)
    for window in windows:
        by_state[window.state].append(window)
    claimable = by_state[WindowState.OPEN] + by_state[WindowState.CLOSING]
    opening = [
        w
        for w in by_state[WindowState.NOT_YET_OPEN]
        if w.opens and (w.opens - on).days <= OPENING_DAYS
    ]

    print(f"Claim windows on {on:%d/%m/%Y}:")
    print(f"  Claim now: {_total(claimable)} across {_batches(claimable)}", end="")
    closing = by_state[WindowState.CLOSING]
    print(f"; {_total(closing)} of it closes within {CLOSING_DAYS} days." if closing else ".")
    print(
        f"  Windows opening in the next {OPENING_DAYS} days: "
        + (f"{_total(opening)} across {_batches(opening)}." if opening else "none.")
    )
    lost_value = sum((claim.value or Decimal(0) for claim in lost), Decimal(0))
    print(
        f"  Written off in the last {LOST_DAYS} days while it could have been claimed: "
        f"{format_inr(lost_value, paise=True)} across {len(lost)} write-offs."
    )
    closed = by_state[WindowState.CLOSED]
    if closed:
        print(
            f"  Windows already closed on stock still held: {_total(closed)} across "
            f"{_batches(closed)}."
        )
    no_terms = by_state[WindowState.NO_TERMS]
    if no_terms:
        companies = sorted({w.company_id for w in no_terms})
        print(
            f"  No return terms on record for {len(companies)} "
            f"{'company' if len(companies) == 1 else 'companies'} with stock that will not sell: "
            f"{', '.join(companies[:8])}{' ...' if len(companies) > 8 else ''}."
        )
    shown = (closing + by_state[WindowState.OPEN] + opening)[: max(1, args.limit)]
    if shown:
        print(
            f"\n  {'Company':<9}{'Product':<22}{'Batch':<10}{'Expiry':<9}{'Units':>7}"
            f"{'Claim value':>15}  {'Window':<23}State"
        )
        for w in shown:
            print(
                f"  {w.company_id:<9}{stock.items[w.batch.item_id].brand[:21]:<22}"
                f"{w.batch.batch_no:<10}{format_expiry(w.batch.expiry):<9}{w.units_to_claim:>7}"
                f"{format_inr(w.claim_value or Decimal(0), paise=True):>15}  "
                f"{w.opens:%d/%m/%y} to {w.closes:%d/%m/%y}    {w.state}"
            )
    return 0


def _draft(args: argparse.Namespace) -> int:
    try:
        drafted = draft_for(args.marg, args.records, company_id=args.company)
    except _FAILURES as error:
        return _fail(error)
    company = drafted.stock.parties[args.company]
    if drafted.claim is None or drafted.posting is None:
        print(f"Nothing can be claimed from {company.name} today.")
        return 0
    claim = drafted.claim
    print(
        f"Claim {claim.number} on {company.name}: {claim.units} units of {claim.batches} "
        f"{'batch' if claim.batches == 1 else 'batches'}, "
        f"{format_inr(claim.taxable_value, paise=True)} + GST "
        f"{format_inr(claim.tax_amount, paise=True)} = {format_inr(claim.total, paise=True)}."
    )
    for line in claim.lines:
        print(
            f"  {drafted.stock.items[line.batch.item_id].brand[:21]:<22}{line.batch.batch_no:<10}"
            f"{format_expiry(line.batch.expiry):<9}{line.location_id:<11}{line.units:>6}"
            f"{format_inr(line.taxable_value, paise=True):>14}"
        )
    planned = drafted.posting
    return request_approval(
        planned,
        records=args.records,
        start=lambda: claiming.ask_to_claim(
            args.marg, args.records, args.company, planned, out=args.out
        ),
        approve_by=args.approve_by,
        notify=notifier(args),
    )


def _mark_return(args: argparse.Namespace) -> int:
    try:
        reason = ReturnReason(
            document_ref=args.document,
            reason=args.reason,
            recorded_by=args.by,
            at=datetime.now(IST),
            note=args.note,
        )
        with RecordStore(args.records) as store:
            recorded = store.save_return_reason(reason)
    except _FAILURES as error:
        return _fail(error)
    document = normalise(args.document)
    if not recorded:
        print(f"{document} is already recorded as {args.reason}.")
        return 0
    print(f"Recorded {document} as {args.reason}, by {args.by}.")
    if args.reason is Reason.BREAKAGE:
        print("Its units can be claimed with `claims breakage draft <company>`.")
    return 0


def _list_returns(args: argparse.Namespace) -> int:
    try:
        with RecordStore(args.records, create=False) as store:
            reasons = store.return_reasons()
        unmarked = [] if args.marg is None else _unmarked(args.marg, reasons, args.limit)
    except _FAILURES as error:
        return _fail(error)
    if reasons:
        print(f"  {'Credit note':<18}{'Reason':<10}{'Recorded':<18}By")
        for reason in reasons:
            print(
                f"  {reason.document_ref:<18}{reason.reason!s:<10}"
                f"{reason.at.astimezone(IST):%d/%m/%Y %H:%M}  {reason.recorded_by}"
            )
    else:
        print("No returns are marked. Record one with `claims returns mark`.")
    if args.marg is not None:
        if not unmarked:
            print("\nEvery return in Marg is marked.")
            return 0
        print(f"\nReturns in Marg with no reason recorded, latest first (of {len(unmarked)}):")
        for document, day, units in unmarked[: max(1, args.limit)]:
            print(f"  {document:<18}{day:%d/%m/%Y}{units:>8} units")
    return 0


def _unmarked(marg: Path, reasons, limit: int) -> list[tuple[str, date, int]]:
    """Sale returns in Marg with no reason recorded, latest first."""
    stock = load_marg(marg)
    marked = {normalise(reason.document_ref) for reason in reasons}
    returns: dict[str, tuple[date, int]] = {}
    for movement in stock.ledger:
        if movement.kind is not MovementType.SALE_RETURN or movement.qty <= 0:
            continue
        if stock.ledger.is_reversed(movement.id):
            continue
        document = normalise(movement.document_ref)
        if document in marked:
            continue
        day, units = returns.get(document, (movement.at.date(), 0))
        returns[document] = (day, units + movement.qty)
    ordered = sorted(returns.items(), key=lambda entry: (entry[1][0], entry[0]), reverse=True)
    return [(document, day, units) for document, (day, units) in ordered]


def _list_breakage(args: argparse.Namespace) -> int:
    try:
        drafted = draft_breakage_for(args.marg, args.records)
    except _FAILURES as error:
        return _fail(error)
    stock, breakage = drafted.stock, drafted.breakage
    if not breakage.to_claim and not breakage.on_sale_shelves:
        print(
            "No breakage is in stock. Mark a chemist's credit note with "
            "`claims returns mark <credit note> --reason breakage`."
        )
        return 0
    costs = batch_costs(stock.ledger, as_of=end_of_day(stock.today))
    by_company: defaultdict[str, list] = defaultdict(list)
    for (key, location_id), units in sorted(breakage.to_claim.items()):
        by_company[key.company_id].append((key, location_id, units))
    total = sum(
        (costs.get(key, Decimal(0)) * units for key, _, units in
         (row for rows in by_company.values() for row in rows)),
        Decimal(0),
    )  # fmt: skip
    noun = "batch" if len(breakage.to_claim) == 1 else "batches"
    print(
        f"Breakage in stock on {stock.today:%d/%m/%Y}: {breakage.units} units of "
        f"{len(breakage.to_claim)} {noun} from {len(by_company)} "
        f"{'company' if len(by_company) == 1 else 'companies'}, {format_inr(total)} at cost."
    )
    rows = [row for company in sorted(by_company) for row in by_company[company]]
    if rows:
        print(f"\n  {'Company':<9}{'Product':<22}{'Batch':<10}{'Expiry':<9}{'Place':<11}"
              f"{'Units':>7}{'At cost':>12}")  # fmt: skip
        for key, location_id, units in rows[: max(1, args.limit)]:
            brand = stock.items[key.item_id].brand if key.item_id in stock.items else key.item_id
            print(
                f"  {key.company_id:<9}{brand[:21]:<22}{key.batch_no:<10}"
                f"{format_expiry(key.expiry):<9}{location_id:<11}{units:>7}"
                f"{format_inr(costs.get(key, Decimal(0)) * units):>12}"
            )
    if breakage.on_sale_shelves:
        print(
            "\nMarked as breakage but still where stock is sold from; move it to the breakage "
            "and expiry shelf in Marg:"
        )
        for (key, location_id), units in sorted(breakage.on_sale_shelves.items()):
            print(f"  {key.batch_no:<10}{location_id:<11}{units:>7} units")
    print("\nClaim it with `claims breakage draft <company>`; only an approval makes the claim.")
    return 0


def _draft_breakage(args: argparse.Namespace) -> int:
    try:
        drafted = draft_breakage_for(args.marg, args.records, company_id=args.company)
    except _FAILURES as error:
        return _fail(error)
    company = drafted.stock.parties[args.company]
    if drafted.claim is None or drafted.posting is None:
        print(f"No breakage of {company.name}'s is in stock to claim.")
        return 0
    claim = drafted.claim
    print(
        f"Breakage claim {claim.number} on {company.name}: {claim.units} units of "
        f"{claim.batches} {'batch' if claim.batches == 1 else 'batches'}, "
        f"{format_inr(claim.taxable_value, paise=True)} + GST "
        f"{format_inr(claim.tax_amount, paise=True)} = {format_inr(claim.total, paise=True)}."
    )
    for line in claim.lines:
        brand = drafted.stock.items[line.batch.item_id].brand
        print(
            f"  {brand[:21]:<22}{line.batch.batch_no:<10}{format_expiry(line.batch.expiry):<9}"
            f"{line.location_id:<11}{line.units:>6}"
            f"{format_inr(line.taxable_value, paise=True):>14}"
        )
    planned = drafted.posting
    return request_approval(
        planned,
        records=args.records,
        start=lambda: breakage_flow.ask_to_claim_breakage(
            args.marg, args.records, args.company, planned, out=args.out
        ),
        approve_by=args.approve_by,
        notify=notifier(args),
    )


def _list_claims(args: argparse.Namespace) -> int:
    try:
        with RecordStore(args.records, create=False) as store:
            claims = store.claims()
            settlements = store.settlements()
    except _FAILURES as error:
        return _fail(error)
    on = args.on or date.today()
    by_claim = defaultdict(list)
    for settlement in settlements:
        by_claim[settlement.claim].append(settlement)
    rows = []
    for claim in claims:
        credited, owed = settled(claim.total, by_claim[claim.number])
        if owed == 0 and not args.all:
            continue
        rows.append((claim, credited, owed))
    if not rows:
        print("No claims are waiting for credit." if not args.all else "No claims are recorded.")
        return 0
    outstanding = sum((owed for _, _, owed in rows), Decimal(0))
    print(
        f"{len(rows)} {'claim' if len(rows) == 1 else 'claims'}; "
        f"{format_inr(outstanding, paise=True)} still to be credited."
    )
    print(
        f"  {'Claim':<17}{'Company':<9}{'Made':<11}{'Age':>5}{'Claimed':>15}"
        f"{'Credited':>15}{'Owed':>15}"
    )
    for claim, credited, owed in sorted(rows, key=lambda row: (row[0].company_id, row[0].made_on)):
        print(
            f"  {claim.number:<17}{claim.company_id:<9}{claim.made_on:%d/%m/%Y} "
            f"{(on - claim.made_on).days:>5}{format_inr(claim.total, paise=True):>15}"
            f"{format_inr(credited, paise=True):>15}{format_inr(owed, paise=True):>15}"
        )
    return 0


def _settle(args: argparse.Namespace) -> int:
    try:
        amount = Decimal(args.amount.replace(",", "").replace("₹", "").strip())
    except InvalidOperation:
        return _fail(f"{args.amount!r} is not an amount in rupees")
    try:
        with RecordStore(args.records, create=False) as store, store.transaction():
            claim = store.claim(args.claim)
            if claim is None:
                raise RecordsError(f"no claim {args.claim} is recorded")
            if args.received < claim.made_on:
                raise ValueError(
                    f"a credit note cannot arrive before claim {claim.number} was made "
                    f"on {claim.made_on:%d/%m/%Y}"
                )
            settlement = Settlement(
                claim.number, args.credit_note.strip(), amount, args.received, args.by.strip()
            )
            saved = store.save_settlement(settlement)
            credited, owed = settled(claim.total, store.settlements(claim.number))
    except _FAILURES as error:
        return _fail(error)
    if not saved:
        print(f"Credit note {settlement.credit_note} is already recorded against {claim.number}.")
    else:
        print(
            f"Recorded credit note {settlement.credit_note} for "
            f"{format_inr(settlement.amount, paise=True)} against {claim.number}."
        )
    if owed:
        print(
            f"{format_inr(credited, paise=True)} of {format_inr(claim.total, paise=True)} "
            f"credited; {format_inr(owed, paise=True)} still owed."
        )
    elif credited > claim.total:
        print(f"Credited {format_inr(credited - claim.total, paise=True)} more than was claimed.")
    else:
        print("The claim is settled in full.")
    return 0


def _total(windows) -> str:
    return format_inr(sum((w.claim_value or Decimal(0) for w in windows), Decimal(0)), paise=True)


def _batches(windows) -> str:
    return f"{len(windows)} {'batch' if len(windows) == 1 else 'batches'}"


def _fail(error: object) -> int:
    print(f"batchward claims: {error}", file=sys.stderr)
    return 1
