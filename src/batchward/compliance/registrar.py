"""Registrar: are a wholesaler's Rule 65 records complete, in order, and kept long enough?

Rule 65(5) of the Drugs Rules, 1945 sets what a wholesale licensee must record:

- every sale is on a cash or credit memo showing the date of sale; the name,
  address and sale licence number of the licensee it was sold to; the name,
  quantity and batch number of the drug; the manufacturer; and the signature of
  the competent person who supervised the sale;
- every purchase is recorded with its date; the supplier's name, address and
  licence number; the name, quantity and batch number of the drug; and the
  manufacturer;
- purchase bills and memos are serially numbered and kept in date order;
- carbon copies of the memos are kept for three years from the date of sale.

This module checks those particulars against the ledger and the party and item
records, and reports every gap an inspector would find. Two limits:

- The competent person's signature is on paper, so it cannot be checked here.
- The Schedule H1 register of prescriber and patient belongs to retail sales
  (Rule 65(3)), so it is not a stockist's register and is not checked.

Cancelled bills — reversed movements — are not memos and are skipped. Bill numbers
commonly start again each financial year, so a bill is its number within the
financial year (1 April to 31 March) it was made in.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from batchward.core.clock import ist_date
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, Item, MovementType, Party, StockMovement

RETENTION_YEARS = 3


class Gap(StrEnum):
    NO_BUYER = "sale with no buyer on the memo"
    UNKNOWN_BUYER = "buyer not in the party records"
    NO_BUYER_LICENCE = "buyer's sale licence number missing"
    NO_BUYER_ADDRESS = "buyer's address missing"
    NO_SUPPLIER = "purchase with no supplier recorded"
    UNKNOWN_SUPPLIER = "supplier not in the party records"
    NO_SUPPLIER_LICENCE = "supplier's licence number missing"
    NO_SUPPLIER_ADDRESS = "supplier's address missing"
    SEVERAL_PARTIES = "one bill names more than one party"
    NO_MANUFACTURER = "manufacturer not in the party records"
    OUT_OF_ORDER = "bill numbered out of date order"


_GAP_ORDER = {gap: index for index, gap in enumerate(Gap)}


class Record(StrEnum):
    SALE_MEMO = "sale memo"
    PURCHASE_BILL = "purchase bill"


@dataclass(frozen=True, slots=True)
class Finding:
    gap: Gap
    record: Record
    """Whether the bill is a sale memo or a purchase bill, which may share a number."""
    document_ref: str
    day: date
    party_id: str | None = None
    batch: BatchKey | None = None


@dataclass(frozen=True, slots=True)
class Rule65Report:
    since: date
    as_of: date
    sale_memos: int
    sale_lines: int
    purchase_bills: int
    purchase_lines: int
    findings: tuple[Finding, ...]
    """One per gap per bill, dated by the bill's first line, in date order. Each names the
    first party or batch on the bill that shows the gap."""
    keep_from: date
    """Memos dated on or after this day must still be kept."""
    memos_past_retention: int
    """Memos older than that, which the rule no longer requires to be kept."""

    @property
    def complete(self) -> bool:
        return not self.findings

    def counts(self) -> dict[Gap, int]:
        totals: defaultdict[Gap, int] = defaultdict(int)
        for finding in self.findings:
            totals[finding.gap] += 1
        return {gap: totals[gap] for gap in Gap if totals[gap]}


def keep_from(as_of: date, years: int = RETENTION_YEARS) -> date:
    """The earliest sale date whose memo must still be kept on ``as_of``."""
    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:  # 29 February, three years before a year with none
        return as_of.replace(year=as_of.year - years, day=28)


def rule65_check(
    ledger: Ledger,
    parties: Mapping[str, Party],
    items: Mapping[str, Item],
    *,
    since: date,
    as_of: date,
) -> Rule65Report:
    """Check every sale memo and purchase bill dated from ``since`` to ``as_of``."""
    if since > as_of:
        raise ValueError("since must not be after as_of")
    sales: defaultdict[tuple[int, str], list[StockMovement]] = defaultdict(list)
    purchases: defaultdict[tuple[int, str], list[StockMovement]] = defaultdict(list)
    first_sale_days: dict[tuple[int, str], date] = {}
    for m in ledger:
        if m.kind not in (MovementType.SALE, MovementType.PURCHASE) or ledger.is_reversed(m.id):
            continue
        day = ist_date(m.at)
        bill = (_financial_year(day), m.document_ref)
        if m.kind is MovementType.SALE:
            first_sale_days[bill] = min(day, first_sale_days.get(bill, day))
        if since <= day <= as_of:
            (sales if m.kind is MovementType.SALE else purchases)[bill].append(m)

    findings: list[Finding] = []
    for bills, record in ((sales, Record.SALE_MEMO), (purchases, Record.PURCHASE_BILL)):
        for (_, document), lines in bills.items():
            lines.sort(key=lambda m: m.at)
            bill = (record, document, ist_date(lines[0].at))
            findings.extend(_party_gaps(bill, lines, parties))
            findings.extend(_manufacturer_gaps(bill, lines, parties, items))
        findings.extend(_out_of_order(bills, record))

    retention_start = keep_from(as_of)
    return Rule65Report(
        since=since,
        as_of=as_of,
        sale_memos=len(sales),
        sale_lines=sum(len(lines) for lines in sales.values()),
        purchase_bills=len(purchases),
        purchase_lines=sum(len(lines) for lines in purchases.values()),
        findings=tuple(sorted(findings, key=_finding_order)),
        keep_from=retention_start,
        memos_past_retention=sum(1 for day in first_sale_days.values() if day < retention_start),
    )


def _financial_year(day: date) -> int:
    """The year in which the Indian financial year holding ``day`` began, on 1 April."""
    return day.year if day.month >= 4 else day.year - 1


def _finding_order(finding: Finding) -> tuple:
    batch = finding.batch
    return (
        finding.day,
        finding.document_ref,
        finding.record,
        _GAP_ORDER[finding.gap],
        finding.party_id or "",
        () if batch is None else (batch.company_id, batch.item_id, batch.batch_no, batch.expiry),
    )


def _party_gaps(
    bill: tuple[Record, str, date], lines: list[StockMovement], parties: Mapping[str, Party]
) -> list[Finding]:
    missing, unknown, licence, address = (
        (Gap.NO_BUYER, Gap.UNKNOWN_BUYER, Gap.NO_BUYER_LICENCE, Gap.NO_BUYER_ADDRESS)
        if bill[0] is Record.SALE_MEMO
        else (
            Gap.NO_SUPPLIER,
            Gap.UNKNOWN_SUPPLIER,
            Gap.NO_SUPPLIER_LICENCE,
            Gap.NO_SUPPLIER_ADDRESS,
        )
    )
    found: dict[Gap, Finding] = {}
    named = dict.fromkeys(m.party_id for m in lines)
    if len(named) > 1:
        found[Gap.SEVERAL_PARTIES] = Finding(Gap.SEVERAL_PARTIES, *bill)
    for party_id in named:
        if party_id is None:
            found.setdefault(missing, Finding(missing, *bill))
            continue
        party = parties.get(party_id)
        if party is None:
            found.setdefault(unknown, Finding(unknown, *bill, party_id))
            continue
        if not (party.drug_licence_no or "").strip():
            found.setdefault(licence, Finding(licence, *bill, party_id))
        if not (party.address or "").strip():
            found.setdefault(address, Finding(address, *bill, party_id))
    return list(found.values())


def _manufacturer_gaps(
    bill: tuple[Record, str, date],
    lines: list[StockMovement],
    parties: Mapping[str, Party],
    items: Mapping[str, Item],
) -> list[Finding]:
    for m in lines:
        item = items.get(m.batch.item_id)
        company_id = item.company_id if item else m.batch.company_id
        if company_id not in parties:
            return [Finding(Gap.NO_MANUFACTURER, *bill, batch=m.batch)]
    return []


def _out_of_order(
    bills: Mapping[tuple[int, str], list[StockMovement]], record: Record
) -> list[Finding]:
    """Bills numbered out of date order within their series and financial year.

    The most bills that are already in order are kept and the rest reported, so one mistyped
    number is reported itself rather than through every correct bill after it. Where several
    choices keep as many bills, the earlier bills are kept.
    """
    series: defaultdict[tuple[int, str, str], list[tuple[datetime, int, str]]] = defaultdict(list)
    for (year, document), lines in bills.items():
        numbered = _serial(document)
        if numbered:
            prefix, number, suffix = numbered
            series[year, prefix, suffix].append((lines[0].at, number, document))
    gaps = []
    for numbered in series.values():
        numbered.sort()
        for position in _outside_longest_run([number for _, number, _ in numbered]):
            first, _, document = numbered[position]
            gaps.append(Finding(Gap.OUT_OF_ORDER, record, document, ist_date(first)))
    return gaps


_TRAILING_NUMBER = re.compile(r"\d+$")
_TRAILING_YEAR = re.compile(
    r"[-/](?:(?:19|20)\d\d|(?P<start>(?:19|20)?\d\d)[-/](?P<end>(?:19|20)?\d\d))$"
)


def _serial(document: str) -> tuple[str, int, str] | None:
    """Split a bill number into the text before its serial, the serial, and the text after.

    The serial is the number the bill ends with, or the number before a closing year
    (``S/0042/2026``) or financial year (``S/0042/25-26``), which names the series.
    """
    body, suffix = document, ""
    year = _TRAILING_YEAR.search(document)
    if year and (year["start"] is None or int(year["end"]) % 100 == (int(year["start"]) + 1) % 100):
        body, suffix = document[: year.start()], document[year.start() :]
    number = _TRAILING_NUMBER.search(body)
    if number is None and suffix:  # a year alone is the serial
        body, suffix = document, ""
        number = _TRAILING_NUMBER.search(body)
    if number is None:
        return None
    return body[: number.start()], int(number.group()), suffix


def _outside_longest_run(numbers: list[int]) -> list[int]:
    """Positions left out of the earliest longest run of numbers that never goes down."""
    # longest[i] is the longest such run starting at position i. Working back from the end,
    # tails[k] is minus the largest number that starts a run of k + 1.
    longest: list[int] = []
    tails: list[int] = []
    for number in reversed(numbers):
        k = bisect_right(tails, -number)
        tails[k : k + 1] = [-number]
        longest.append(k + 1)
    longest.reverse()
    wanted, last, outside = max(longest, default=0), -1, []
    for position, (number, length) in enumerate(zip(numbers, longest, strict=True)):
        if length == wanted and number >= last:
            wanted, last = wanted - 1, number
        else:
            outside.append(position)
    return outside
