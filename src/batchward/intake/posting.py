"""Posting a receipt: the approval, what it received against its order, and the files, together.

A receipt ready to post becomes a ``Posting``: the files approval would write
for Marg's import and to send (ADR 0016), and the digest of exactly those files,
which is what a person approves. ``post`` records the approval and writes the
files in one transaction of the records database, so neither happens without
the other, and posting the same thing twice writes nothing the second time.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from batchward.core.approvals import Approval, Posted, Posting, digest, write_files
from batchward.intake.receipt import Receipt
from batchward.intake.voucher import approval_id, debit_note, purchase_voucher
from batchward.records.store import RecordsError, RecordStore
from batchward.reporting.inr import format_inr

KIND = "purchase voucher"


def posting(receipt: Receipt, *, received_on: date) -> Posting:
    """The files a ready receipt posts. ValueError if it is not ready."""
    voucher = purchase_voucher(receipt)
    note = debit_note(receipt, received=received_on)
    name = re.sub(r"[^A-Za-z0-9]+", "-", receipt.check.invoice.invoice_no).strip("-")
    files = {f"{name}.marg-purchase.csv": voucher}
    if note is not None:
        files[f"{name}.debit-note.txt"] = note
    units = sum(line.paid + line.free for line in receipt.lines)
    lines = sum(1 for line in receipt.lines if line.paid + line.free)
    summary = (
        f"bill {receipt.check.invoice.invoice_no}: {units} units on {lines} "
        f"{'line' if lines == 1 else 'lines'}"
        + (
            f"; debit note {format_inr(receipt.debit_total, paise=True)}"
            if receipt.debit_note
            else ""
        )
    )
    return Posting(approval_id(receipt.check), files, summary, digest(voucher + (note or "")))


def post(
    store: RecordStore,
    receipt: Receipt,
    planned: Posting,
    *,
    received_on: date,
    approved_by: str,
    at: datetime,
    out: Path,
) -> Posted:
    """Record the approval of ``planned`` and write its files, all or nothing.

    Inside a caller's transaction, the caller's other records are kept or undone
    with it. ``receipt`` is the receipt matched again just before posting; RecordsError if
    it is no longer what was approved, or if another bill on the same order was
    approved since it was matched. An approval already recorded for the same
    posting writes nothing; one for a different form of the bill is refused.
    """
    if not receipt.ready:
        raise RecordsError(f"{planned.approval_id} is no longer ready to post")
    if posting(receipt, received_on=received_on).digest != planned.digest:
        raise RecordsError(
            f"{planned.approval_id} has changed since it was put up for approval; match it again"
        )
    approval = Approval(
        id=planned.approval_id,
        kind=KIND,
        approved_by=approved_by,
        at=at,
        digest=planned.digest,
        summary=planned.summary,
    )
    with store.atomically():
        order = receipt.order
        if order is not None and (
            store.received_against(order.number, excluding=approval.id) != receipt.earlier
        ):
            raise RecordsError(
                f"another bill on order {order.number} was approved while this one was "
                "being matched; match it again"
            )
        if not store.save_approval(approval):
            earlier = store.approval(approval.id)
            assert earlier is not None
            return Posted(earlier, ())
        if order is not None:
            store.save_received(approval.id, order.number, receipt.received())
        # Written inside the transaction: if a file cannot be written, the approval is not
        # recorded either, and approving again tries afresh.
        written = write_files(out, planned.files)
    return Posted(approval, written)
