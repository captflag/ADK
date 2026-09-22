"""Making an expiry claim on approval (ADR 0005, 0018).

``claims draft`` drafts a claim on one company and puts it up for approval
through the shared approval workflow. On approval the claim is drafted again
from Marg and the records, and made only if it is still exactly what the person
saw: the claim is recorded, and its sheet, letter and Marg return voucher are
written.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from batchward.agents.approval_flow import UNUSABLE, Flow, Outcome, ask, not_carried_out
from batchward.claims.claim import KIND
from batchward.claims.submission import draft_for, submit
from batchward.core.approvals import ApprovalRequest, Decision, Posting
from batchward.core.clock import IST
from batchward.records.store import RecordsError, RecordStore


def make_claim(node_input: dict, claim: dict, posting: dict, out: str, records: str) -> dict:
    """Draft the claim again and make it, recording the approval, the claim and the files."""
    request, by = node_input["request"], node_input["by"]
    at = datetime.now(IST)
    planned = Posting(**posting)
    try:
        drafted = draft_for(Path(claim["marg"]), Path(records), company_id=claim["company_id"])
        if drafted.claim is None or drafted.posting is None:
            raise RecordsError(
                "nothing can be claimed from this company any longer; see claims windows"
            )
        with RecordStore(records) as store, store.transaction():
            store.save_decision(Decision(request, True, by, at))
            posted = submit(
                store,
                drafted.claim,
                drafted.posting,
                planned,
                approved_by=by,
                at=at,
                out=Path(out),
            )
    except UNUSABLE as error:
        return not_carried_out(Path(records), request, by, at, error)
    if not posted.written:
        first = posted.approval
        return Outcome(
            request,
            "posted",
            by,
            f"already approved by {first.approved_by} on {first.at:%d/%m/%Y %H:%M}",
        ).to_output()
    return Outcome(request, "posted", by, written=tuple(map(str, posted.written))).to_output()


FLOW = Flow(kind=KIND, app="claiming", carry_out=make_claim)


async def ask_to_claim(
    marg: Path, records: Path, company_id: str, planned: Posting, *, out: Path
) -> ApprovalRequest:
    """Put a drafted claim up for approval; returns the request made."""
    state = {
        "claim": {"marg": str(marg), "company_id": company_id},
        "posting": asdict(planned),
        "out": str(out),
    }
    return await ask(KIND, state, records=records)
