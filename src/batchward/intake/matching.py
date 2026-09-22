"""Which supplier and which item a printed invoice means.

Invoices describe products their own way: "AZINIL 500 TAB 3'S", "Azinil-500
Tablets". A line is matched to an item only when exactly one item's brand
appears in it whole, among the supplier's items where the supplier is known,
and a pack size printed in the line settles a brand sold in several packs.
Anything else is left for a person to decide; a wrong match would put stock on
the wrong item and every later check on the wrong batch.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from batchward.compliance.nppa import pack_contents
from batchward.core.models import Item, Party, PartyKind


@dataclass(frozen=True, slots=True)
class ItemMatch:
    item: Item | None
    candidates: tuple[Item, ...]
    """Every item the line could mean; one when matched, none or several when not."""


def match_supplier(name: str, gstin: str, parties: Iterable[Party]) -> Party | None:
    """The company an invoice is from: by GSTIN, or failing that by its name printed whole."""
    companies = [party for party in parties if party.kind is PartyKind.COMPANY]
    wanted = _compact(gstin)
    if wanted:
        by_gstin = [party for party in companies if party.gstin and _compact(party.gstin) == wanted]
        if len(by_gstin) == 1:
            return by_gstin[0]
    words = _words(name)
    by_name = [party for party in companies if _contains(words, _words(party.name))]
    return by_name[0] if len(by_name) == 1 else None


def match_item(description: str, items: Iterable[Item], *, company_id: str | None) -> ItemMatch:
    """The one item a printed description names, if exactly one fits."""
    words = _words(description)
    candidates = [item for item in items if _contains(words, _words(item.brand))]
    if company_id is not None and any(item.company_id == company_id for item in candidates):
        candidates = [item for item in candidates if item.company_id == company_id]
    if len(candidates) > 1:
        by_pack = [
            item
            for item in candidates
            if _printed_numbers(words, _words(item.brand))
            & {held for held in pack_contents(item.unit).values() if held != 1}
        ]
        if len(by_pack) == 1:
            candidates = by_pack
    ordered = tuple(sorted(candidates, key=lambda item: item.id))
    return ItemMatch(ordered[0] if len(ordered) == 1 else None, ordered)


def _printed_numbers(words: list[str], brand: list[str]) -> set[Decimal]:
    """Numbers in a description other than those in the brand, which may be a pack size."""
    for i in range(len(words) - len(brand) + 1):
        if words[i : i + len(brand)] == brand:
            words = words[:i] + words[i + len(brand) :]
            break
    return {Decimal(word) for word in words if word[0].isdigit()}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+|\d+(?:\.\d+)?", text.lower())


def _contains(words: list[str], phrase: list[str]) -> bool:
    size = len(phrase)
    return size > 0 and any(words[i : i + size] == phrase for i in range(len(words) - size + 1))


def _compact(text: str) -> str:
    return "".join(text.split()).upper()
