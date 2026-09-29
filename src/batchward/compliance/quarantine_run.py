"""Drafting a quarantine transfer from the records and Marg, and making it on approval.

Drafting reads what is blocked, what is where, and what has already been moved,
so that drafting twice in a day draws up the same transfer and approving it
twice writes nothing twice (ADR 0005). The transfer is drafted again when the
approval comes back and made only if it is still exactly what the person saw:
stock moves, and a batch nobody has blocked must not be moved on yesterday's
say-so (ADR 0029).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from batchward.agents.data import load_marg, unsellable_locations
from batchward.bridge.transfer_voucher import render_transfer
from batchward.compliance.quarantine import (
    GODOWN,
    KIND,
    Transfer,
    draft_transfer,
    transfer_posting,
)
from batchward.core.approvals import Approval, Posted, Posting, write_files
from batchward.records.store import RecordsError, RecordStore


@dataclass(frozen=True, slots=True)
class Drafted:
    """A transfer as drafted, with what approving it would write."""

    transfer: Transfer
    posting: Posting | None
    """None when there is nothing to move."""


def draft_for(
    marg: Path, records: Path, *, godown: str = GODOWN, on: date | None = None
) -> Drafted:
    """Today's transfer for a Marg database and the records that hold its holds."""
    stock = load_marg(marg, unsellable=unsellable_locations())
    with RecordStore(records, create=False) as store:
        log = store.hold_log()
        moved = store.quarantine_moves()
    transfer = draft_transfer(
        log,
        stock.ledger,
        stock.locations,
        on=on or stock.today,
        godown=godown,
        moved=moved,
    )
    if transfer.empty:
        return Drafted(transfer, None)
    voucher = render_transfer(transfer, stock.items)
    return Drafted(transfer, transfer_posting(transfer, stock.items, voucher=voucher))


def submit(
    store: RecordStore,
    transfer: Transfer,
    current: Posting,
    planned: Posting,
    *,
    approved_by: str,
    at: datetime,
    out: Path,
    kind: str = KIND,
) -> Posted:
    """Record the approved transfer and write its voucher, all or nothing.

    ``current`` is the transfer drafted again just before; RecordsError if it is
    no longer what was approved. The same transfer approved again writes nothing.
    """
    if current.digest != planned.digest:
        raise RecordsError(
            f"transfer {transfer.number} has changed since it was put up for approval: "
            "stock has moved or a hold has changed. Draft it again."
        )
    approval = Approval(planned.approval_id, kind, approved_by, at, planned.digest, planned.summary)
    with store.atomically():
        if not store.save_approval(approval):
            earlier = store.approval(approval.id)
            assert earlier is not None
            return Posted(earlier, ())
        store.save_transfer(transfer, approval.id)
        written = write_files(out, planned.files)
    return Posted(approval, written)
