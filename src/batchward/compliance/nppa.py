"""NPPA ceiling price notifications, read from their tables and converted to the unit sold.

NPPA notifies a ceiling price in an S.O. published in the Gazette, as a table
with one row per scheduled formulation: its name, its dosage form and strength,
the unit priced (most often "1 Tablet" or "1 ml") and the ceiling price per that
unit, excluding GST. The notification's number and the date it takes effect
head the table. The layout assumed here is that table copied to CSV; like the
Marg layout, it is an assumption until real notifications are loaded (ADR 0013).

A stockist sells strips, bottles and vials, not tablets, and the Price Guard
holds ceilings per unit sold (ADR 0011). So each notified row is matched to the
items stocked by molecule and strength, and its price is multiplied out by each
item's pack: ₹6.42 for 1 tablet is ₹64.20 for a strip of 10 tablets. Nothing is
guessed. A pack whose contents cannot be read in the notified unit, and an item
that matches a notified formulation but is not marked as scheduled, are reported
for a person to settle.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from batchward.compliance.prices import CeilingPrice
from batchward.core.models import Item

_COLUMNS = {
    "nameofthescheduledformulation": "formulation",
    "nameoftheformulation": "formulation",
    "dosageformstrength": "dosage_form_and_strength",
    "dosageformandstrength": "dosage_form_and_strength",
    "unit": "unit",
    "ceilingpricers": "price",
    "ceilingpriceinrs": "price",
    "ceilingprice": "price",
}
"""Header text with everything but letters removed, for each column needed, as the
notifications spell it."""
_PRECISION = Decimal("0.0001")


class NotificationError(ValueError):
    """A notification table that cannot be read, naming the row."""


@dataclass(frozen=True, slots=True)
class NotifiedCeiling:
    row: int
    """The line of the table it came from, counting the header as line 1."""
    formulation: str
    dosage_form_and_strength: str
    unit: str
    """The unit the price is for, as notified, e.g. "1 Tablet"."""
    price: Decimal
    """Per notified unit, excluding GST."""

    @property
    def strength(self) -> str:
        """The strength, without the dosage form written before it: "Tablet 10 mg" is "10 mg"."""
        found = re.search(r"\d.*", self.dosage_form_and_strength)
        return found.group(0).strip() if found else ""


@dataclass(frozen=True, slots=True)
class Unconverted:
    notified: NotifiedCeiling
    item: Item
    reason: str


@dataclass(frozen=True, slots=True)
class CeilingImport:
    prices: tuple[CeilingPrice, ...]
    """One per formulation and pack stocked, per unit sold."""
    not_stocked: tuple[NotifiedCeiling, ...]
    """Notified formulations no item matches."""
    unconverted: tuple[Unconverted, ...]
    """Items of a notified formulation whose pack cannot be priced from the notified unit."""
    not_marked_scheduled: tuple[Item, ...]
    """Items of a notified formulation that the billing data does not mark as scheduled, so
    the Price Guard would not check them."""


def read_notification(lines: Iterable[str]) -> list[NotifiedCeiling]:
    """The rows of a notification table in CSV, with a header naming its columns."""
    reader = csv.reader(lines)
    header = next(reader, None)
    if header is None:
        raise NotificationError("the notification table is empty")
    positions = {}
    for index, title in enumerate(header):
        key = _COLUMNS.get(re.sub(r"[^a-z]", "", title.lower()))
        if key is not None:
            positions[key] = index
    missing = [
        title
        for title, key in (
            ("Name of the Scheduled Formulation", "formulation"),
            ("Dosage form & Strength", "dosage_form_and_strength"),
            ("Unit", "unit"),
            ("Ceiling Price (Rs.)", "price"),
        )
        if key not in positions
    ]
    if missing:
        raise NotificationError(f"the header has no column {', '.join(map(repr, missing))}")

    rows = []
    for number, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue
        values = {
            key: cells[index].strip() if index < len(cells) else ""
            for key, index in positions.items()
        }
        blank = [key for key, value in values.items() if not value]
        if blank:
            raise NotificationError(f"line {number} has no {', '.join(sorted(blank))}")
        rows.append(
            NotifiedCeiling(
                row=number,
                formulation=values["formulation"],
                dosage_form_and_strength=values["dosage_form_and_strength"],
                unit=values["unit"],
                price=_price(values["price"], number),
            )
        )
    return rows


def convert(
    notified: Iterable[NotifiedCeiling],
    items: Iterable[Item],
    *,
    reference: str,
    effective_from: date,
) -> CeilingImport:
    """Ceiling prices per unit sold for every stocked pack of every notified formulation."""
    stocked = list(items)
    prices: dict[tuple[str, str, str], CeilingPrice] = {}
    not_stocked, unconverted, unscheduled = [], [], {}
    for row in notified:
        matching = [
            item
            for item in stocked
            if _same(item.molecule, row.formulation) and _same(item.strength, row.strength)
        ]
        if not matching:
            not_stocked.append(row)
            continue
        for item in sorted(matching, key=lambda item: item.id):
            if not item.dpco_scheduled:
                unscheduled[item.id] = item
            per_unit_sold = _per_unit_sold(row, item)
            if isinstance(per_unit_sold, str):
                unconverted.append(Unconverted(row, item, per_unit_sold))
                continue
            price = CeilingPrice(
                molecule=item.molecule,
                strength=item.strength,
                unit=item.unit,
                ceiling=per_unit_sold,
                effective_from=effective_from,
                reference=reference,
            )
            earlier = prices.setdefault(price.formulation, price)
            if earlier.ceiling != price.ceiling:
                raise NotificationError(
                    f"line {row.row} prices {item.molecule} {item.strength}, {item.unit} at "
                    f"{price.ceiling}, but another line prices it at {earlier.ceiling}"
                )
    return CeilingImport(
        prices=tuple(prices.values()),
        not_stocked=tuple(not_stocked),
        unconverted=tuple(unconverted),
        not_marked_scheduled=tuple(unscheduled.values()),
    )


def pack_contents(unit: str) -> dict[str, Decimal]:
    """What one unit sold holds, by dosage unit: "strip of 10 tablets" holds 10 tablets and is
    1 strip; "10 ml vial" holds 10 ml and is 1 vial."""
    text = " ".join(unit.lower().split())
    contents: dict[str, Decimal] = {}
    if found := re.fullmatch(r"(.+?) of (\d+(?:\.\d+)?) ?([a-z ]+?)", text):
        contents[_singular(found[3])] = Decimal(found[2])
        text = found[1]
    elif found := re.fullmatch(r"(\d+(?:\.\d+)?) ?(ml|l|g|gm) (.+)", text):
        contents[{"gm": "g"}.get(found[2], found[2])] = Decimal(found[1])
        text = found[3]
    if re.fullmatch(r"[a-z]+(?: [a-z]+)*", text):
        contents.setdefault(_singular(text), Decimal(1))
        contents.setdefault(_singular(text.split()[-1]), Decimal(1))
    return contents


def _per_unit_sold(row: NotifiedCeiling, item: Item) -> Decimal | str:
    unit = re.sub(r"^(?:each|per) ", "", " ".join(row.unit.lower().split()))
    found = re.fullmatch(r"(?:(\d+(?:\.\d+)?) ?)?([a-z ]+?)", unit)
    if not found:
        return f"the notified unit {row.unit!r} is not a quantity of a dosage unit"
    quantity = Decimal(found[1] or 1)
    dosage_unit = {"gm": "g"}.get(found[2], _singular(found[2]))
    held = pack_contents(item.unit).get(dosage_unit)
    if held is None:
        return f"the pack {item.unit!r} does not say how many of {row.unit!r} it holds"
    if quantity <= 0:
        return f"the notified unit {row.unit!r} has no quantity"
    return (row.price * held / quantity).quantize(_PRECISION)


def _singular(word: str) -> str:
    word = word.strip()
    if word.endswith("ches") or word.endswith("shes"):
        return word[:-2]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def _same(ours: str | None, notified: str) -> bool:
    def normal(text: str | None) -> str:
        return "".join((text or "").lower().split())

    return bool(normal(ours)) and normal(ours) == normal(notified)


def _price(text: str, row: int) -> Decimal:
    cleaned = text.replace("₹", "").replace("Rs.", "").replace("Rs", "").replace(",", "").strip()
    try:
        price = Decimal(cleaned)
    except InvalidOperation:
        price = None
    if price is None or not price.is_finite() or price <= 0:
        raise NotificationError(f"line {row} has ceiling price {text!r}, not a positive amount")
    return price
