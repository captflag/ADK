"""Making a breakage claim on approval (ADR 0005, 0025).

``claims breakage draft`` drafts a claim for what chemists sent back broken and
puts it up for approval through the shared approval workflow. On approval the
claim is drafted again from Marg and the records, and made only if it is still
exactly what the person saw: the claim is recorded, and its sheet, letter and
Marg return voucher are written.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from batchward.agents.approval_flow import UNUSABLE, Flow, Outcome, ask, not_carried_out
from batchward.claims.breakage import KIND
from batchward.claims.submission import draft_breakage_for, submit
from batchward.core.approvals import ApprovalRequest, Decision, Posting
from batchward.core.clock import IST
from batchward.records.store import RecordsError, RecordStore


def make_breakage_claim(node_input: dict, claim: dict, posting: dict, out: str, records: str):
    """Draft the claim again and make it, recording the approval, the claim and the files."""
    request, by = node_input["request"], node_input["by"]
    at = datetime.now(IST)
    planned = Posting(**posting)
    try:
        drafted = draft_breakage_for(
            Path(claim["marg"]), Path(records), company_id=claim["company_id"]
        )
        if drafted.claim is None or drafted.posting is None:
            raise RecordsError(
                "no breakage of this company's is left to claim; see claims breakage list"
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
                kind=KIND,
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


FLOW = Flow(kind=KIND, app="breakage", carry_out=make_breakage_claim)


async def ask_to_claim_breakage(
    marg: Path, records: Path, company_id: str, planned: Posting, *, out: Path
) -> ApprovalRequest:
    """Put a drafted breakage claim up for approval; returns the request made."""
    state = {
        "claim": {"marg": str(marg), "company_id": company_id},
        "posting": asdict(planned),
        "out": str(out),
    }
    return await ask(KIND, state, records=records)
