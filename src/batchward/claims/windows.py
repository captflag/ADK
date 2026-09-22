"""Claim windows: which expiring stock can be claimed from its company, and until when.

A company takes back expiring stock only inside a window around each batch's
expiry (ADR 0018). What is worth claiming is the stock that will not sell before
it expires: everything on the breakage and expiry shelf, stock already past
expiry, and what the forecast says will be left over on the sellable shelves
(the expiry risk of ``analysis.expiry``). Its claim value is its cost times the
share the company credits.

Stock written off while its window was still open is a claim lost: the company
would have credited it, and nobody asked.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from batchward.analysis.costs import PAISA
from batchward.analysis.expiry import expiry_exposure
from batchward.claims.terms import ReturnTerms, TermsTable
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, Location, MovementType

CLOSING_DAYS = 15
"""A window closing within this many days is flagged: the blueprint's 15-day trigger."""


class WindowState(StrEnum):
    CLOSING = "closing soon"
    OPEN = "open"
    NOT_YET_OPEN = "opens later"
    CLOSED = "closed"
    NO_TERMS = "no terms on record"


_ORDER = list(WindowState)


@dataclass(frozen=True, slots=True)
class ClaimWindow:
    batch: BatchKey
    units_to_claim: int
    """Units that will not sell before expiry, less any already claimed."""
    by_location: tuple[tuple[str, int], ...]
    """Where those units are, returns shelf first: what a return would take from where."""
    cost: Decimal | None
    """Cost per unit; None when no purchase rate is on record."""
    terms: ReturnTerms | None
    opens: date | None
    closes: date | None
    state: WindowState

    @property
    def company_id(self) -> str:
        return self.batch.company_id

    @property
    def claim_value(self) -> Decimal | None:
        """What the company would credit, before GST: cost x units x its credit share."""
        if self.cost is None or self.terms is None:
            return None
        return (self.cost * self.units_to_claim * self.terms.credit_percent / 100).quantize(PAISA)


@dataclass(frozen=True, slots=True)
class LostClaim:
    batch: BatchKey
    units: int
    written_off_on: date
    closes: date
    value: Decimal | None
    """What the company would have credited, before GST."""


def claim_windows(
    ledger: Ledger,
    costs: Mapping[BatchKey, Decimal],
    locations: Iterable[Location],
    daily_rates: Mapping[str, float],
    terms: TermsTable,
    *,
    on: date,
    claimed: Mapping[tuple[BatchKey, str], int] | None = None,
) -> list[ClaimWindow]:
    """Every batch with stock that will not sell before expiry, and its claim window.

    ``claimed`` is units already claimed from each batch and location but still in
    stock, as when Marg has not yet imported the return: they are not claimed again.
    Batches whose window is closing come first, then open ones, each by value.
    """
    locations = tuple(locations)
    returns_shelves = {location.id for location in locations if not location.sellable}
    at_risk: defaultdict[BatchKey, dict[str, int]] = defaultdict(dict)
    for risk in expiry_exposure(ledger, costs, locations, daily_rates, on=on):
        units = risk.units_at_risk - (claimed or {}).get((risk.batch, risk.location_id), 0)
        if units > 0:
            at_risk[risk.batch][risk.location_id] = units

    windows = []
    for key, places in at_risk.items():
        entry = terms.in_force(key.company_id, on)
        opens = closes = None
        if entry is None:
            state = WindowState.NO_TERMS
        else:
            opens, closes = entry.window(key.expiry)
            if on < opens:
                state = WindowState.NOT_YET_OPEN
            elif on > closes:
                state = WindowState.CLOSED
            elif (closes - on).days <= CLOSING_DAYS:
                state = WindowState.CLOSING
            else:
                state = WindowState.OPEN
        by_location = tuple(
            sorted(places.items(), key=lambda place: (place[0] not in returns_shelves, place[0]))
        )
        windows.append(
            ClaimWindow(
                batch=key,
                units_to_claim=sum(places.values()),
                by_location=by_location,
                cost=costs.get(key),
                terms=entry,
                opens=opens,
                closes=closes,
                state=state,
            )
        )
    return sorted(
        windows,
        key=lambda w: (
            _ORDER.index(w.state),
            -(w.claim_value or Decimal(0)),
            w.closes or date.max,
            w.batch,
        ),
    )


def lost_claims(
    ledger: Ledger,
    costs: Mapping[BatchKey, Decimal],
    terms: TermsTable,
    *,
    since: datetime,
    until: datetime,
) -> list[LostClaim]:
    """Stock written off between ``since`` and ``until`` while its claim window was open."""
    lost = []
    for movement in ledger:
        if movement.kind is not MovementType.WRITE_OFF or not since <= movement.at <= until:
            continue
        if ledger.is_reversed(movement.id):
            continue
        day = movement.at.date()
        entry = terms.in_force(movement.batch.company_id, day)
        if entry is None:
            continue
        opens, closes = entry.window(movement.batch.expiry)
        if not opens <= day <= closes:
            continue
        units = -movement.qty
        cost = costs.get(movement.batch)
        value = (
            None if cost is None else (cost * units * entry.credit_percent / 100).quantize(PAISA)
        )
        lost.append(LostClaim(movement.batch, units, day, closes, value))
    return sorted(lost, key=lambda claim: (-(claim.value or Decimal(0)), claim.batch))
