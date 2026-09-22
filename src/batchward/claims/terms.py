"""Each company's terms for taking back expiring stock, as dated records (ADR 0018).

A company credits stock returned for expiry only inside a window around each
batch's expiry: from some days before it until some days after, at a share of
its value. Terms differ by company and change over time, and none are
published centrally, so they are data the stockist enters, dated like ceiling
prices (ADR 0011): the terms in force on a day are the latest to take effect on
or before it.
"""

from __future__ import annotations

import bisect
import csv
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from batchward.core.printed import printed_date


@dataclass(frozen=True, slots=True)
class ReturnTerms:
    company_id: str
    opens_days_before_expiry: int
    """How long before a batch expires the company starts taking it back."""
    closes_days_after_expiry: int
    """How long after a batch expires the company still takes it back; 0 for not at all."""
    credit_percent: Decimal
    """The share of the stock's value the company credits, from 0 to 100."""
    effective_from: date
    reference: str
    """Where the terms come from: a circular, a letter, the area manager's word."""

    def __post_init__(self) -> None:
        if not self.company_id.strip() or not self.reference.strip():
            raise ValueError("return terms need a company and a reference")
        if self.opens_days_before_expiry < 0 or self.closes_days_after_expiry < 0:
            raise ValueError("a claim window cannot open after expiry or close before it")
        if not Decimal(0) < self.credit_percent <= Decimal(100):
            raise ValueError("the credit must be more than 0% and at most 100% of the value")

    def window(self, expiry: date) -> tuple[date, date]:
        """The first and last days a batch expiring on ``expiry`` can be claimed."""
        return (
            expiry - timedelta(days=self.opens_days_before_expiry),
            expiry + timedelta(days=self.closes_days_after_expiry),
        )


class TermsTable:
    """Return terms by company, each in force from its date until the next."""

    def __init__(self, terms: Iterable[ReturnTerms] = ()) -> None:
        by_company: defaultdict[str, list[ReturnTerms]] = defaultdict(list)
        for entry in terms:
            by_company[entry.company_id].append(entry)
        self._terms: dict[str, list[ReturnTerms]] = {}
        for company_id, entries in by_company.items():
            entries.sort(key=lambda entry: entry.effective_from)
            for earlier, later in pairwise(entries):
                if earlier.effective_from == later.effective_from:
                    raise ValueError(
                        f"two sets of return terms for {company_id} take effect on "
                        f"{later.effective_from.isoformat()}"
                    )
            self._terms[company_id] = entries

    def __len__(self) -> int:
        return sum(len(entries) for entries in self._terms.values())

    def in_force(self, company_id: str, on: date) -> ReturnTerms | None:
        entries = self._terms.get(company_id, [])
        index = bisect.bisect_right([entry.effective_from for entry in entries], on)
        return entries[index - 1] if index else None


_COLUMNS = {
    "company": "company_id",
    "companyid": "company_id",
    "companycode": "company_id",
    "opensdaysbeforeexpiry": "opens",
    "opens": "opens",
    "closesdaysafterexpiry": "closes",
    "closes": "closes",
    "credit": "credit",
    "creditpercent": "credit",
    "effectivefrom": "effective_from",
    "from": "effective_from",
    "reference": "reference",
}


class TermsFileError(ValueError):
    """A terms file that cannot be read, naming the line."""


def read_terms(lines: Iterable[str]) -> list[ReturnTerms]:
    """Return terms from CSV: company, days before and after expiry, credit %, date, reference."""
    reader = csv.reader(lines)
    header = next(reader, None)
    if header is None:
        raise TermsFileError("the terms file is empty")
    positions: dict[str, int] = {}
    for index, title in enumerate(header):
        key = _COLUMNS.get(re.sub(r"[^a-z]", "", title.lower()))
        if key is not None:
            positions.setdefault(key, index)
    missing = [key for key in _COLUMNS.values() if key not in positions]
    if missing:
        raise TermsFileError(f"the terms file has no column for {sorted(set(missing))}")
    terms = []
    for number, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue
        value = {
            key: cells[index].strip() if index < len(cells) else ""
            for key, index in positions.items()
        }
        effective = printed_date(value["effective_from"])
        if effective is None:
            raise TermsFileError(f"line {number}: {value['effective_from']!r} is not a date")
        try:
            terms.append(
                ReturnTerms(
                    company_id=value["company_id"],
                    opens_days_before_expiry=int(value["opens"]),
                    closes_days_after_expiry=int(value["closes"]),
                    credit_percent=Decimal(value["credit"].rstrip("%").strip()),
                    effective_from=effective,
                    reference=value["reference"],
                )
            )
        except (ValueError, InvalidOperation) as error:
            raise TermsFileError(f"line {number}: {error}") from error
    return terms
