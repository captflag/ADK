"""Placing a purchase order on approval (ADR 0005, 0020).

``orders draft`` drafts an order on one company and puts it up for approval
through the shared approval workflow. On approval the order is planned again
from Marg and the records, with the same cover and lead time, and placed only if
it is still exactly what the person saw: the order is recorded, and its sheet
and message are written.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from batchward.agents.approval_flow import UNUSABLE, Flow, Outcome, ask, not_carried_out
from batchward.buying.order import KIND
from batchward.buying.planning import place, plan_for
from batchward.buying.suggest import Policy
from batchward.core.approvals import ApprovalRequest, Decision, Posting
from batchward.core.clock import IST
from batchward.records.store import RecordsError, RecordStore


def place_order(node_input: dict, order: dict, posting: dict, out: str, records: str) -> dict:
    """Plan the order again and place it, recording the approval, the order and the files."""
    request, by = node_input["request"], node_input["by"]
    at = datetime.now(IST)
    planned = Posting(**posting)
    try:
        replanned = plan_for(
            Path(order["marg"]),
            Path(records),
            policy=Policy(order["cover_days"], order["lead_days"]),
            company_id=order["company_id"],
        )
        if replanned.order is None or replanned.posting is None:
            raise RecordsError("nothing needs ordering from this company any longer")
        with RecordStore(records) as store, store.transaction():
            store.save_decision(Decision(request, True, by, at))
            posted = place(
                store,
                replanned.order,
                replanned.posting,
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


FLOW = Flow(kind=KIND, app="ordering", carry_out=place_order)


async def ask_to_order(
    marg: Path, records: Path, company_id: str, policy: Policy, planned: Posting, *, out: Path
) -> ApprovalRequest:
    """Put a drafted order up for approval; returns the request made."""
    state = {
        "order": {
            "marg": str(marg),
            "company_id": company_id,
            "cover_days": policy.cover_days,
            "lead_days": policy.lead_days,
        },
        "posting": asdict(planned),
        "out": str(out),
    }
    return await ask(KIND, state, records=records)
