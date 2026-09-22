"""``batchward recall``: record notices, block batches, lift blocks, and report.

Stock is read from a Marg database (ADR 0006); notices and holds are kept in
Batchward's own records database (ADR 0010). A notice is entered by command, or
a whole CDSCO drug alert list is imported at once (ADR 0014).
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from batchward.agents.data import DataUnavailableError, StockData, load_marg
from batchward.bridge.marg_contract import MargLayoutError
from batchward.bridge.marg_import import MargDataError
from batchward.bridge.marg_layout import format_expiry, parse_expiry
from batchward.compliance.cdsco import read_alert_list
from batchward.compliance.recall import Candidate, RecallClass, RecallNotice
from batchward.core.clock import IST
from batchward.core.holds import HoldError
from batchward.core.models import BatchKey
from batchward.records import recalls
from batchward.records.store import RecordsError, RecordStore
from batchward.reporting.chemist_notice import chemist_notices
from batchward.reporting.recall import format_moment, render_recall_report


def add_recall_commands(commands: argparse._SubParsersAction) -> None:
    recall = commands.add_parser(
        "recall", help="record recall notices, block batches automatically, and report on recalls"
    )
    actions = recall.add_subparsers(dest="recall_command", required=True)

    receive = actions.add_parser(
        "receive", help="record a recall notice and block every batch it names exactly"
    )
    _stock_and_records(receive)
    receive.add_argument("--reference", required=True, help="the notice's own reference")
    receive.add_argument("--source", required=True, help="who issued it, e.g. the manufacturer")
    receive.add_argument(
        "--class", dest="recall_class", required=True, choices=[str(c) for c in RecallClass]
    )
    receive.add_argument("--batch", required=True, help="batch number as printed in the notice")
    receive.add_argument("--manufacturer", help="manufacturer as named in the notice")
    receive.add_argument("--product", help="product as named in the notice")
    receive.add_argument("--expiry", type=parse_expiry, help="expiry as printed, MM/YYYY")
    receive.add_argument(
        "--received",
        type=moment,
        help="when it arrived, e.g. 2026-02-12T09:15 (IST); default now, or when it was first "
        "recorded if it is being received again",
    )
    receive.set_defaults(handler=_receive)

    alerts = actions.add_parser(
        "import-alerts",
        help="record every row of a CDSCO drug alert list as a notice, blocking exact matches",
    )
    _stock_and_records(alerts)
    alerts.add_argument("--csv", type=Path, required=True, help="the alert list's table as CSV")
    alerts.add_argument(
        "--list",
        dest="list_reference",
        required=True,
        help='the list\'s name, e.g. "CDSCO drug alert, August 2026"; rows are numbered under it',
    )
    alerts.add_argument(
        "--class",
        dest="recall_class",
        required=True,
        choices=[str(c) for c in RecallClass],
        help="the recall class to apply; the lists do not give one",
    )
    alerts.add_argument("--received", type=moment, help="when the list arrived (IST); default now")
    alerts.set_defaults(handler=_import_alerts)

    status = actions.add_parser("status", help="report where a recorded recall stands")
    _stock_and_records(status)
    status.add_argument("--reference", required=True)
    status.add_argument("--as-of", type=moment, help="report as of this moment; default now")
    status.set_defaults(handler=_status)

    notices = actions.add_parser(
        "notices",
        help="draft a recall notice for each chemist still holding a recalled batch, to sign",
    )
    _stock_and_records(notices)
    notices.add_argument("--reference", required=True)
    notices.add_argument(
        "--from",
        dest="sender",
        help="the stockist's name, address and drug licence number, as the notices should show",
    )
    notices.add_argument(
        "--out", type=Path, help="write one file per chemist into this folder; default print them"
    )
    notices.add_argument("--force", action="store_true", help="overwrite notices already written")
    notices.add_argument("--as-of", type=moment, help="draft as of this moment; default now")
    notices.set_defaults(handler=_notices)

    release = actions.add_parser("release", help="lift a hold; the hold stays on record")
    release.add_argument("--records", type=Path, required=True)
    release.add_argument("--hold", required=True, help="hold id, e.g. H00001")
    release.add_argument("--reason", required=True)
    release.add_argument("--by", required=True, help="who is lifting the hold")
    release.add_argument("--at", type=moment, help="when; default now")
    release.set_defaults(handler=_release)

    listing = actions.add_parser("list", help="list recorded notices and holds")
    listing.add_argument("--records", type=Path, required=True)
    listing.set_defaults(handler=_list)


def moment(text: str) -> datetime:
    """An ISO date and time; one written without a zone is Indian Standard Time."""
    try:
        value = datetime.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not a date and time: {text!r}") from error
    return value if value.tzinfo else value.replace(tzinfo=IST)


def _stock_and_records(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--marg", type=Path, required=True, help="Marg database to read stock from")
    parser.add_argument("--records", type=Path, required=True, help="Batchward records database")


def _receive(args: argparse.Namespace) -> int:
    with _failures():
        now = datetime.now(IST)
        _not_in_future(args.received, now, "--received")
        stock = load_marg(args.marg)
        with RecordStore(args.records) as store:
            recorded = store.notice(args.reference)
            received_at = args.received or (recorded.received_at if recorded else now)
            notice = RecallNotice(
                reference=args.reference,
                source=args.source,
                recall_class=RecallClass(args.recall_class),
                received_at=received_at,
                batch_no=args.batch,
                manufacturer=args.manufacturer,
                product=args.product,
                expiry=args.expiry,
            )
            # Blocks are dated when they are placed, which is now, never back to receipt.
            recall = recalls.receive_notice(
                store,
                notice,
                ledger=stock.ledger,
                items=stock.items,
                parties=stock.parties,
                at=now,
                batches=stock.batches,
            )
        print(
            f"Notice {notice.reference}, Class {notice.recall_class} from {notice.source}, "
            f"received {format_moment(notice.received_at)}"
        )
        if recall.holds:
            print(f"Blocked automatically at {format_moment(now)}:")
            for hold in recall.holds:
                print(f"  {hold.id}  {_describe(hold.batch, stock)}")
        elif recall.match.exact:
            print(
                "Already recorded; its batches were blocked when it was first received, "
                "and a block a person lifted stays lifted."
            )
        else:
            print("No batch ever held matches it exactly, so nothing was blocked.")
        _print_review(recall.match.review, stock)
        return 0
    return 1


def _import_alerts(args: argparse.Namespace) -> int:
    with _failures():
        now = datetime.now(IST)
        _not_in_future(args.received, now, "--received")
        stock = load_marg(args.marg)
        try:
            with args.csv.open(encoding="utf-8-sig", newline="") as lines:
                alerts = read_alert_list(lines)
        except OSError as error:
            raise ValueError(f"cannot read {args.csv}: {error}") from error
        new = unmatched = 0
        blocked, already, review, failed = [], [], [], []
        with RecordStore(args.records) as store:
            for alert in alerts:
                recorded = store.notice(f"{args.list_reference} #{alert.serial}")
                notice = alert.notice(
                    list_reference=args.list_reference,
                    received_at=args.received or (recorded.received_at if recorded else now),
                    recall_class=RecallClass(args.recall_class),
                )
                try:
                    recall = recalls.receive_notice(
                        store,
                        notice,
                        ledger=stock.ledger,
                        items=stock.items,
                        parties=stock.parties,
                        at=now,
                        batches=stock.batches,
                    )
                except (RecordsError, HoldError, ValueError) as error:
                    failed.append((alert, error))
                    continue
                new += recorded is None
                blocked += [(alert, hold.id, hold.batch) for hold in recall.holds]
                if recall.match.exact and not recall.holds:
                    already += [(alert, batch) for batch in recall.match.exact]
                review += [(alert, candidate) for candidate in recall.match.review]
                unmatched += not (recall.match.exact or recall.match.review)

        recorded_count = len(alerts) - len(failed)
        print(
            f"{args.list_reference}: {len(alerts)} rows, {new} recorded as new notices"
            + (f", {recorded_count - new} already recorded" if recorded_count > new else "")
        )
        if blocked:
            print(f"Blocked automatically at {format_moment(now)}:")
            for alert, hold_id, batch in blocked:
                print(f"  #{alert.serial}  {hold_id}  {_describe(batch, stock)}")
        if already:
            print("Already blocked or released when the row was first recorded:")
            for alert, batch in already:
                print(f"  #{alert.serial}  {_describe(batch, stock)}")
        if review:
            print("Raised for review, not blocked:")
            for alert, candidate in review:
                print(f"  #{alert.serial}  {_describe(candidate.batch, stock)}")
                for reason in candidate.reasons:
                    print(f"    {reason}")
        print(f"No batch ever held resembles the other {unmatched} rows.")
        unreadable = [alert for alert in alerts if alert.expiry is None]
        if unreadable:
            print("Expiry could not be read, so these rows can only raise batches for review:")
            for alert in unreadable:
                print(f"  #{alert.serial}  {alert.batch_no}: {alert.expiry_as_listed!r}")
        for alert, error in failed:
            print(
                f"batchward recall: line {alert.line} (#{alert.serial}): {error}", file=sys.stderr
            )
        return 1 if failed else 0
    return 1


def _status(args: argparse.Namespace) -> int:
    with _failures():
        stock = load_marg(args.marg)
        with RecordStore(args.records, create=False) as store:
            result = recalls.recall_status(
                store,
                args.reference,
                ledger=stock.ledger,
                items=stock.items,
                parties=stock.parties,
                as_of=args.as_of or datetime.now(IST),
                batches=stock.batches,
            )
            log = store.hold_log()
        if not result.reports:
            print(f"Notice {args.reference} blocked no batch.")
        for report in result.reports:
            print(render_recall_report(report, items=stock.items, parties=stock.parties))
            print()
        for hold in result.holds:
            release = log.release_of(hold.id)
            state = (
                "in force"
                if release is None
                else f"released {format_moment(release.at)} by {release.released_by}: "
                f"{release.reason}"
            )
            print(f"Hold {hold.id} on {hold.batch.batch_no}, {state}")
        if result.unblocked:
            print("Named exactly by the notice but NOT blocked; receive the notice again to block:")
            for batch in result.unblocked:
                print(f"  {_describe(batch, stock)}")
        _print_review(result.review, stock)
        return 0
    return 1


def _notices(args: argparse.Namespace) -> int:
    with _failures():
        stock = load_marg(args.marg)
        with RecordStore(args.records, create=False) as store:
            result = recalls.recall_status(
                store,
                args.reference,
                ledger=stock.ledger,
                items=stock.items,
                parties=stock.parties,
                as_of=args.as_of or datetime.now(IST),
                batches=stock.batches,
            )
        if not result.reports:
            print(f"Notice {args.reference} blocked no batch, so there is no one to notify.")
            return 0
        drafts = {
            (report.batch.batch_no, party_id): text
            for report in result.reports
            for party_id, text in chemist_notices(
                report,
                ledger=stock.ledger,
                items=stock.items,
                parties=stock.parties,
                sender=args.sender,
            ).items()
        }
        if args.out is None:
            print("\n\n".join(drafts.values()))
        else:
            _write_notices(args, drafts)
        outstanding = sum(report.outstanding for report in result.reports)
        print(
            f"Drafted {len(drafts)} {'notice' if len(drafts) == 1 else 'notices'} to chemists "
            f"still holding {outstanding:,} units. Nothing has been sent: each needs the "
            "competent person's signature.",
            file=sys.stderr if args.out is None else sys.stdout,
        )
        return 0
    return 1


def _write_notices(args: argparse.Namespace, drafts: dict[tuple[str, str], str]) -> None:
    reference = re.sub(r"[^A-Za-z0-9]+", "-", args.reference).strip("-")
    files = {
        args.out / f"{reference}_{batch_no}_{party_id}.txt": text
        for (batch_no, party_id), text in drafts.items()
    }
    existing = sorted(path.name for path in files if path.exists())
    if existing and not args.force:
        raise ValueError(
            f"{len(existing)} notices already written in {args.out}, such as {existing[0]}; "
            "pass --force to overwrite them"
        )
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        for path, text in files.items():
            path.write_text(text + "\n", encoding="utf-8")
    except OSError as error:
        raise ValueError(f"cannot write notices to {args.out}: {error}") from error
    print(f"Wrote {len(files)} draft notices to {args.out}")


def _release(args: argparse.Namespace) -> int:
    now = datetime.now(IST)
    with _failures(), RecordStore(args.records, create=False) as store:
        _not_in_future(args.at, now, "--at")
        release = recalls.release_hold(
            store,
            args.hold,
            at=args.at or now,
            reason=args.reason,
            released_by=args.by,
        )
        print(f"Released hold {release.hold_id} at {format_moment(release.at)}.")
        return 0
    return 1


def _list(args: argparse.Namespace) -> int:
    with _failures(), RecordStore(args.records, create=False) as store:
        notices, log = store.notices(), store.hold_log()
        if not notices:
            print("No recall notices recorded.")
        for notice in notices:
            print(
                f"{notice.reference}  Class {notice.recall_class}  batch {notice.batch_no}  "
                f"from {notice.source}, received {format_moment(notice.received_at)}"
            )
            for hold in (h for h in log if h.reference == notice.reference):
                released = log.release_of(hold.id)
                state = "in force" if released is None else "released"
                print(f"  {hold.id}  {hold.status} {hold.batch.batch_no}  {state}")
        return 0
    return 1


def _not_in_future(moment: datetime | None, now: datetime, option: str) -> None:
    if moment is not None and moment > now:
        raise ValueError(f"{option} {format_moment(moment)} is in the future")


class _failures:
    """Turn the errors a person can fix into a message and exit status 1."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind, error, traceback) -> bool:
        if isinstance(error, KeyError) or not isinstance(error, _EXPECTED):
            return False
        print(f"batchward recall: {error}", file=sys.stderr)
        return True


_EXPECTED = (
    DataUnavailableError,
    MargDataError,
    MargLayoutError,
    RecordsError,
    HoldError,
    LookupError,
    ValueError,
    sqlite3.Error,
)


def _describe(batch: BatchKey, stock: StockData) -> str:
    item = stock.items.get(batch.item_id)
    company = stock.parties.get(batch.company_id)
    return (
        f"{batch.batch_no}, expiry {format_expiry(batch.expiry)}, "
        f"{item.brand if item else batch.item_id} from "
        f"{company.name if company else batch.company_id}"
    )


def _print_review(candidates: tuple[Candidate, ...], stock: StockData) -> None:
    if not candidates:
        return
    print("Raised for review, not blocked:")
    for candidate in candidates:
        print(f"  {_describe(candidate.batch, stock)}")
        for reason in candidate.reasons:
            print(f"    {reason}")
