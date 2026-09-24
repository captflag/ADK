"""Whole cases: orders rounded to what a company will supply (ADR 0023).

Companies ship most products in whole cases, and Marg's item master does not
say how many units a case holds. The case sizes are kept in Batchward's
records (ADR 0010), each in force from a date, as a company can change its
packing.

An order is rounded to whole cases by one of two rules:

- **up**: enough whole cases to hold at least what the order needs;
- **nearest**: the nearest whole number of cases, and at least one.

Rounding up never orders less than the rule asked for, but for a slow mover
in a big case it can bring in months of stock; rounding to the nearest keeps
closer to the cover and relies on the next order to make up a shortfall.
Replayed over simulated demand (``batchward cover-backtest --cases``), rounding
to the nearest held about 5% less stock but placed 15-18% more order lines and
served a little less, so Batchward rounds up, and says when one case holds
more than a quarter's demand, for a person to check it will sell in time.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum


class Rounding(StrEnum):
    UP = "up"
    NEAREST = "nearest"


ROUNDING = Rounding.UP
"""How Batchward rounds, from the replay recorded in ADR 0023."""
SLOW_CASE_DAYS = 90
"""A case holding more than this many days of demand is pointed out on the order."""


def to_cases(units: int, case: int, rounding: Rounding = ROUNDING) -> int:
    """Units to order in whole cases of ``case``, for an order needing ``units``."""
    if case < 1:
        raise ValueError("a case holds at least one unit")
    if units <= 0:
        return 0
    if rounding is Rounding.UP:
        cases = math.ceil(units / case)
    else:
        cases = int((Decimal(units) / case).to_integral_value(ROUND_HALF_UP))
    return max(1, cases) * case


@dataclass(frozen=True, slots=True)
class CaseSize:
    item_id: str
    units: int
    """Units in one case."""
    effective_from: date
    reference: str
    """Where the size comes from: a company's price list, a delivery, a letter."""

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("a case size needs the product's code")
        if self.units < 1:
            raise ValueError(f"a case of {self.item_id} must hold at least one unit")
        if not self.reference.strip():
            raise ValueError(f"the case size of {self.item_id} needs a reference")


class CaseTable:
    """Case sizes, each in force from its date until a later one for the same product."""

    def __init__(self, sizes: Iterable[CaseSize] = ()) -> None:
        self._by_item: defaultdict[str, list[CaseSize]] = defaultdict(list)
        for size in sizes:
            self._by_item[size.item_id].append(size)
        for history in self._by_item.values():
            history.sort(key=lambda size: size.effective_from)

    def __len__(self) -> int:
        return sum(len(history) for history in self._by_item.values())

    def __iter__(self) -> Iterator[CaseSize]:
        for item_id in sorted(self._by_item):
            yield from self._by_item[item_id]

    def in_force(self, item_id: str, on: date) -> CaseSize | None:
        """The case size in force for a product on a day, or None if none is on record."""
        current = None
        for size in self._by_item.get(item_id, ()):
            if size.effective_from > on:
                break
            current = size
        return current


COLUMNS = ("Product code", "Units per case", "Effective from", "Reference")


class CaseSizesFileError(ValueError):
    """A case sizes file that cannot be read, naming the line."""


def read_case_sizes(lines: Iterable[str]) -> list[CaseSize]:
    """Case sizes from CSV with the columns in ``COLUMNS``; dates as DD/MM/YYYY."""
    rows = csv.DictReader(lines)
    missing = [column for column in COLUMNS if column not in (rows.fieldnames or ())]
    if missing:
        raise CaseSizesFileError(f"no column {', '.join(missing)}; expected {', '.join(COLUMNS)}")
    sizes = []
    for number, row in enumerate(rows, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue
        try:
            units = int((row["Units per case"] or "").strip())
            effective = datetime.strptime((row["Effective from"] or "").strip(), "%d/%m/%Y")
            sizes.append(
                CaseSize(
                    item_id=(row["Product code"] or "").strip(),
                    units=units,
                    effective_from=effective.date(),
                    reference=(row["Reference"] or "").strip(),
                )
            )
        except ValueError as error:
            raise CaseSizesFileError(f"line {number}: {error}") from error
    return sizes
