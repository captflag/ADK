"""Receiving a delivery on approval (ADR 0005, 0016).

``intake receive --out`` matches a delivery and, when it is ready to post, puts
it up for approval through the shared approval workflow. On approval the
delivery is matched again from the same files and posted only if it is still
exactly what the person saw.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from batchward.agents.approval_flow import UNUSABLE, Flow, Outcome, ask, not_carried_out
from batchward.core.approvals import ApprovalRequest, Decision, Posting
from batchward.core.clock import IST
from batchward.intake.delivery import Delivery, match_delivery
from batchward.intake.posting import KIND, post
from batchward.records.store import RecordStore


def post_receipt(node_input: dict, delivery: dict, posting: dict, out: str) -> dict:
    """Match the delivery again and post it, recording the approval with the files."""
    request, by = node_input["request"], node_input["by"]
    at = datetime.now(IST)
    planned = Posting(**posting)
    matched = Delivery.from_state(delivery)
    records = Path(str(delivery["records"]))
    try:
        receipt, _ = match_delivery(matched)
        with RecordStore(records) as store, store.transaction():
            store.save_decision(Decision(request, True, by, at))
            posted = post(
                store,
                receipt,
                planned,
                received_on=matched.received_on,
                approved_by=by,
                at=at,
                out=Path(out),
            )
    except UNUSABLE as error:
        return not_carried_out(records, request, by, at, error)
    if not posted.written:
        first = posted.approval
        return Outcome(
            request,
            "posted",
            by,
            f"already approved by {first.approved_by} on {first.at:%d/%m/%Y %H:%M}",
        ).to_output()
    return Outcome(request, "posted", by, written=tuple(map(str, posted.written))).to_output()


FLOW = Flow(kind=KIND, app="receiving", carry_out=post_receipt)


async def ask_to_post(delivery: Delivery, planned: Posting, *, out: Path) -> ApprovalRequest:
    """Put a matched delivery up for approval; returns the request made."""
    if delivery.records is None:
        raise ValueError("asking for approval needs the records database")
    state = {"delivery": delivery.to_state(), "posting": asdict(planned), "out": str(out)}
    return await ask(KIND, state, records=delivery.records)
