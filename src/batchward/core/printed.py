"""Dates as they are printed on packs, invoices and lists, where only the month counts."""

from __future__ import annotations

import calendar
import re
from datetime import date

_MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_abbr) if name}


def expiry_month(text: str) -> date | None:
    """The last day of the month a printed expiry names, or None if it names no month.

    Packs, invoices and lists write it many ways: "08/2027", "8/27", "Aug-2027",
    "August 2027", "2027-08", "31/08/2027". Only the month and year count.
    """
    text = text.strip().lower().rstrip(".")
    patterns = (
        (r"(\d{1,2})[-/. ](\d{4}|\d{2})", lambda m: (int(m[1]), m[2])),
        (r"(\d{4})[-/.](\d{1,2})", lambda m: (int(m[2]), m[1])),
        (r"\d{1,2}[-/.](\d{1,2})[-/.](\d{4}|\d{2})", lambda m: (int(m[1]), m[2])),
        (r"([a-z]{3})[a-z]*\.?[-/.,' ]*(\d{4}|\d{2})", lambda m: (_MONTHS.get(m[1], 0), m[2])),
    )
    for pattern, parts in patterns:
        found = re.fullmatch(pattern, text)
        if found:
            month, year = parts(found)
            year = int(year) + (2000 if len(year) == 2 else 0)
            if 1 <= month <= 12:
                return date(year, month, calendar.monthrange(year, month)[1])
            return None
    return None


def printed_date(text: str) -> date | None:
    """The day a printed date names, or None if it names no day.

    Indian papers write the day first: "12/02/2026", "12-02-26", "12.02.2026",
    "12-Feb-2026", "12 February 2026". An ISO "2026-02-12" is read too.
    """
    text = " ".join(text.strip().lower().split())
    day = month = year = None
    if found := re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})", text):
        day, month, year = int(found[1]), int(found[2]), found[3]
    elif found := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        year, month, day = found[1], int(found[2]), int(found[3])
    elif found := re.fullmatch(r"(\d{1,2})[-/. ]([a-z]{3})[a-z]*\.?[-/.,' ]*(\d{4}|\d{2})", text):
        day, month, year = int(found[1]), _MONTHS.get(found[2], 0), found[3]
    if year is None:
        return None
    try:
        return date(int(year) + (2000 if len(year) == 2 else 0), month, day)
    except ValueError:
        return None
