"""Drafting a claim from Marg and the records, and recording it once approved (ADR 0018).

A claim is drafted from the day's stock, forecast and return terms, less what
earlier claims already took but Marg has not yet shown leaving. It is put up for
approval like a delivery (ADR 0005); on approval it is drafted again and made
only if it is still exactly what the person saw, with the approval, the claim
and its files recorded together.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from batchward.agents.data import StockData, load_marg
from batchward.analysis.costs import batch_costs
from batchward.analysis.routing import daily_rates
from batchward.claims.claim import KIND, Claim, claim_posting, draft_claim
from batchward.claims.terms import TermsTable
from batchward.claims.windows import ClaimWindow, claim_windows
from batchward.core.approvals import Approval, Posted, Posting, write_files
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, MovementType
from batchward.records.store import RecordsError, RecordStore


@dataclass(frozen=True, slots=True)
class Drafted:
    stock: StockData
    windows: list[ClaimWindow]
    claim: Claim | None
    posting: Posting | None


def still_in_stock(claims: Iterable[Claim], ledger: Ledger) -> dict[tuple[BatchKey, str], int]:
    """Units claimed from each batch and place that Marg does not yet show returned.

    Marg records the return when it imports the claim's return voucher, as a
    purchase return under the claim's number.
    """
    returned: defaultdict[tuple[str, BatchKey, str], int] = defaultdict(int)
    for movement in ledger:
        if movement.kind is MovementType.PURCHASE_RETURN and not ledger.is_reversed(movement.id):
            key = (movement.document_ref, movement.batch, movement.location_id)
            returned[key] += -movement.qty
    pending: defaultdict[tuple[BatchKey, str], int] = defaultdict(int)
    for claim in claims:
        for line in claim.lines:
            gone = returned[(claim.number, line.batch, line.location_id)]
            taken = min(gone, line.units)
            returned[(claim.number, line.batch, line.location_id)] -= taken
            if line.units > taken:
                pending[(line.batch, line.location_id)] += line.units - taken
    return dict(pending)


def first_bought(ledger: Ledger) -> dict[BatchKey, date]:
    """The day each batch was first bought."""
    first: dict[BatchKey, date] = {}
    for movement in ledger:
        if movement.kind is MovementType.PURCHASE:
            day = movement.at.date()
            if movement.batch not in first or day < first[movement.batch]:
                first[movement.batch] = day
    return first


def windows_for(stock: StockData, terms: TermsTable, claims: Iterable[Claim]) -> list[ClaimWindow]:
    """Claim windows for the stock held today, less what earlier claims already took."""
    return claim_windows(
        stock.ledger,
        batch_costs(stock.ledger),
        stock.locations,
        daily_rates(stock.ledger, on=stock.today),
        terms,
        on=stock.today,
        claimed=still_in_stock(claims, stock.ledger),
    )


def draft_for(marg: Path, records: Path, *, company_id: str | None = None) -> Drafted:
    """Claim windows for the stock in Marg today, and a claim on ``company_id`` if given."""
    stock = load_marg(marg)
    with RecordStore(records, create=False) as store:
        terms = store.terms_table()
        claims = store.claims()
    windows = windows_for(stock, terms, claims)
    claim = posting = None
    if company_id is not None:
        company = stock.parties.get(company_id)
        if company is None:
            raise ValueError(f"no company {company_id} in Marg")
        claim = draft_claim(
            windows,
            stock.items,
            company_id=company_id,
            on=stock.today,
            bought_on=first_bought(stock.ledger),
        )
        if claim is not None:
            posting = claim_posting(claim, company, stock.items)
    return Drafted(stock, windows, claim, posting)


def submit(
    store: RecordStore,
    claim: Claim,
    current: Posting,
    planned: Posting,
    *,
    approved_by: str,
    at: datetime,
    out: Path,
) -> Posted:
    """Record the approved claim and write its files, all or nothing.

    ``current`` is the claim drafted again just before; RecordsError if it is no
    longer what was approved. The same claim approved again writes nothing.
    """
    if current.digest != planned.digest:
        raise RecordsError(
            f"claim {claim.number} has changed since it was put up for approval; draft it again"
        )
    approval = Approval(planned.approval_id, KIND, approved_by, at, planned.digest, planned.summary)
    with store.atomically():
        if not store.save_approval(approval):
            earlier = store.approval(approval.id)
            assert earlier is not None
            return Posted(earlier, ())
        store.save_claim(claim, approval.id)
        written = write_files(out, planned.files)
    return Posted(approval, written)
