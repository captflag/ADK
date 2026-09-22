"""Recall notices to the chemists a recalled batch was supplied to, drafted for signature.

A recall is not finished until every chemist holding the batch has been told to
stop selling it and send it back. Batchward drafts one notice per chemist with
units still to return, naming the bills the batch went out on, so the chemist
can find it. The notices are drafts: the competent person checks and signs
them, and nothing here sends anything (ADR 0004).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from batchward.compliance.recall_report import ChemistRecovery, RecallReport
from batchward.core.clock import ist_date
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, Item, MovementType, Party
from batchward.core.trace import effective_kind
from batchward.reporting.inr import group_indian
from batchward.reporting.recall import format_moment

SENDER_PLACEHOLDER = "[stockist's name, address and drug licence number]"


@dataclass(frozen=True, slots=True)
class SuppliedBill:
    document_ref: str
    day: date
    """The day of the bill's first line of this batch."""
    units: int
    """Units of the batch on the bill, net of reversals."""


def supplied_bills(
    ledger: Ledger, batch: BatchKey, party_id: str, *, report: RecallReport
) -> list[SuppliedBill]:
    """The bills a batch went to a chemist on, as they stood at the report's moment."""
    units: dict[str, int] = {}
    first: dict[str, date] = {}
    for m in ledger.movements_for(batch):
        if m.at > report.as_of or m.party_id != party_id:
            continue
        if effective_kind(ledger, m) is not MovementType.SALE:
            continue
        document = m.document_ref
        if m.reverses is not None:
            document = ledger.get(m.reverses).document_ref
        units[document] = units.get(document, 0) - m.qty
        first.setdefault(document, ist_date(m.at))
    return [
        SuppliedBill(document, first[document], qty)
        for document, qty in sorted(units.items(), key=lambda entry: (first[entry[0]], entry[0]))
        if qty > 0
    ]


def chemist_notices(
    report: RecallReport,
    *,
    ledger: Ledger,
    items: Mapping[str, Item],
    parties: Mapping[str, Party],
    sender: str | None = None,
) -> dict[str, str]:
    """A draft notice for every chemist with units still to return, by party id."""
    return {
        chemist.party_id: render_chemist_notice(
            report, chemist, ledger=ledger, items=items, parties=parties, sender=sender
        )
        for chemist in report.chemists
        if chemist.outstanding
    }


def render_chemist_notice(
    report: RecallReport,
    chemist: ChemistRecovery,
    *,
    ledger: Ledger,
    items: Mapping[str, Item],
    parties: Mapping[str, Party],
    sender: str | None = None,
) -> str:
    notice, batch = report.notice, report.batch
    item = items.get(batch.item_id)
    company = parties.get(batch.company_id)
    party = parties.get(chemist.party_id)
    product = (
        f"{item.brand} ({item.molecule} {item.strength}), {item.unit}" if item else batch.item_id
    )
    due = report.completion.due
    lines = [
        "RECALL NOTICE TO CHEMIST",
        "Draft for the competent person to check and sign. Nothing has been sent.",
        "",
        f"{'To':10}{party.name if party else chemist.party_id}",
        f"{'Address':10}{(party.address if party else None) or '-'}",
        f"{'Licence':10}{(party.drug_licence_no if party else None) or '-'}",
        f"{'From':10}{sender or SENDER_PLACEHOLDER}",
        "",
        f"Recall of {product}",
        f"Batch {batch.batch_no}, expiry {batch.expiry:%m/%Y}, manufactured by "
        f"{company.name if company else batch.company_id}",
        f"Class {notice.recall_class} recall notice {notice.reference} from {notice.source}, "
        f"received by us {format_moment(notice.received_at)}.",
        "",
        "Our records show this batch was supplied to you on these bills:",
    ]
    bills = supplied_bills(ledger, batch, chemist.party_id, report=report)
    for bill in bills:
        lines.append(f"  Bill {bill.document_ref:24}{bill.day:%d/%m/%Y}  {_units(bill.units)}")
    returned_before = sum(bill.units for bill in bills) - chemist.due
    if returned_before > 0:
        lines.append(f"  {'Returned to us before the notice':41}{_units(returned_before)}")
    lines += [
        f"  {'Returned to us so far':41}{_units(chemist.recovered)}",
        f"  {'Still to return':41}{_units(chemist.outstanding)}",
        "",
        "Please:",
        "  1. Stop selling this batch at once.",
        '  2. Keep every unit you hold apart from other stock, marked "Recalled - not for sale".',
    ]
    if report.as_of > due:
        lines.append(
            f"  3. Return them to us at once. The recall was due to be complete by "
            f"{format_moment(due)}."
        )
    else:
        lines.append(f"  3. Return them to us by {format_moment(due)}.")
    lines += [
        "  4. If you no longer hold some of them, tell us how many and where they went, so the",
        "     recall can be reconciled.",
        "",
        f"Prepared from our records as of {format_moment(report.as_of)}.",
        "",
        "Signature of the competent person: ______________________",
    ]
    return "\n".join(lines)


def _units(units: int) -> str:
    return f"{group_indian(str(units))} {'unit' if units == 1 else 'units'}"
