"""Breakage chemists send back: why stock came back, and the claim for it (ADR 0025).

Marg records a chemist's return onto the breakage and expiry shelf without
saying which it was, so Batchward keeps the reason itself (ADR 0010): a credit
note is marked as breakage, expiry or something else, by a person who knows.

Units that came back on a credit note marked breakage are claimable from their
company now, whatever their expiry, at the share of value the company's return
terms credit (ADR 0018). They are taken out of the expiry claim windows, so the
same units are never claimed twice, and a claim is drafted, approved and
recorded exactly as an expiry claim is, with its number beginning ``BR``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from batchward.claims.claim import Claim, ClaimLine, claim_number
from batchward.claims.terms import TermsTable
from batchward.core.clock import end_of_day
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, Item, Location, MovementType

KIND = "breakage claim"
PREFIX = "BR"
"""Breakage claim numbers begin with this, expiry claims with CL."""


class Reason(StrEnum):
    BREAKAGE = "breakage"
    EXPIRY = "expiry"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ReturnReason:
    """Why stock came back on one of the stockist's credit notes."""

    document_ref: str
    """The credit note the return came back on, as Marg numbers it."""
    reason: Reason
    recorded_by: str
    at: datetime
    note: str = ""

    def __post_init__(self) -> None:
        if not self.document_ref.strip():
            raise ValueError("a return's reason needs the credit note it came back on")
        if not self.recorded_by.strip():
            raise ValueError(f"the reason for {self.document_ref} needs who recorded it")
        if self.at.tzinfo is None:
            raise ValueError("the time a reason was recorded must be timezone-aware")


Units = dict[tuple[BatchKey, str], int]


@dataclass(frozen=True, slots=True)
class Breakage:
    """Units marked as breakage that are still in stock, by batch and place."""

    to_claim: Units
    """On shelves stock is never sold from: what a breakage claim takes."""
    on_sale_shelves: Units
    """Still where stock is sold from: move them off the shelf before claiming."""

    @property
    def units(self) -> int:
        return sum(self.to_claim.values())

    def batches(self) -> set[BatchKey]:
        return {key for key, _ in self.to_claim}


def normalise(document_ref: str) -> str:
    """A credit note number as it is matched: without spaces, in capitals."""
    return "".join(document_ref.split()).upper()


def breakage_in_stock(
    ledger: Ledger,
    reasons: Iterable[ReturnReason],
    locations: Iterable[Location],
    *,
    on: date,
) -> Breakage:
    """Units returned on credit notes marked breakage that are still in stock on ``on``.

    A return whose units have since left, written off or sent back to the
    company, is not counted again: what came back is capped at what is still there.
    """
    marked = {
        normalise(reason.document_ref) for reason in reasons if reason.reason is Reason.BREAKAGE
    }
    returned: defaultdict[tuple[BatchKey, str], int] = defaultdict(int)
    for movement in ledger:
        if movement.kind is not MovementType.SALE_RETURN or movement.qty <= 0:
            continue
        if ledger.is_reversed(movement.id) or normalise(movement.document_ref) not in marked:
            continue
        returned[(movement.batch, movement.location_id)] += movement.qty
    balances = ledger.balances(end_of_day(on))
    sellable = {location.id for location in locations if location.sellable}
    to_claim: Units = {}
    on_shelves: Units = {}
    for where, units in returned.items():
        left = min(units, balances.get(where, 0))
        if left <= 0:
            continue
        (on_shelves if where[1] in sellable else to_claim)[where] = left
    return Breakage(to_claim, on_shelves)


def draft_breakage(
    breakage: Breakage,
    costs: Mapping[BatchKey, Decimal],
    terms: TermsTable,
    items: Mapping[str, Item],
    *,
    company_id: str,
    on: date,
    claimed: Mapping[tuple[BatchKey, str], int] | None = None,
    bought_on: Mapping[BatchKey, date] | None = None,
) -> Claim | None:
    """A breakage claim on one company for what is on its returns shelves, or None.

    Only batches with a cost on record and a company with terms are claimed, as
    an expiry claim is. ``claimed`` is units earlier claims took that Marg has
    not yet shown leaving.
    """
    lines = []
    for (key, location_id), units in sorted(breakage.to_claim.items()):
        if key.company_id != company_id or key.item_id not in items:
            continue
        cost = costs.get(key)
        company_terms = terms.in_force(company_id, on)
        left = units - (claimed or {}).get((key, location_id), 0)
        if cost is None or company_terms is None or left <= 0:
            continue
        lines.append(
            ClaimLine(
                batch=key,
                location_id=location_id,
                units=left,
                rate=cost,
                credit_percent=company_terms.credit_percent,
                gst_rate=items[key.item_id].gst_rate,
                bought_on=(bought_on or {}).get(key),
            )
        )
    if not lines:
        return None
    lines.sort(key=lambda line: (line.batch.item_id, line.batch.expiry, line.batch.batch_no))
    return Claim(claim_number(company_id, on, prefix=PREFIX), company_id, on, tuple(lines))
