"""Moving blocked stock out of the selling godowns on approval (ADR 0005, ADR 0029).

``recall quarantine draft`` draws up today's transfer and puts it up for
approval through the shared approval workflow. On approval the transfer is
drafted again from Marg and the records, and made only if it is still exactly
what the person saw: the transfer is recorded and the voucher for Marg's import
is written. Nothing has moved in Marg until somebody imports that voucher.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from batchward.agents.approval_flow import UNUSABLE, Flow, Outcome, ask, not_carried_out
from batchward.compliance.quarantine import KIND
from batchward.compliance.quarantine_run import draft_for, submit
from batchward.core.approvals import ApprovalRequest, Decision, Posting
from batchward.core.clock import IST
from batchward.records.store import RecordsError, RecordStore


def make_transfer(node_input: dict, transfer: dict, posting: dict, out: str, records: str):
    """Draft the transfer again and make it, recording the approval and the voucher."""
    request, by = node_input["request"], node_input["by"]
    at = datetime.now(IST)
    planned = Posting(**posting)
    try:
        drafted = draft_for(Path(transfer["marg"]), Path(records), godown=transfer["godown"])
        if drafted.posting is None:
            raise RecordsError(
                "there is nothing left to move: no blocked batch is in a selling godown"
            )
        with RecordStore(records) as store, store.transaction():
            store.save_decision(Decision(request, True, by, at))
            posted = submit(
                store,
                drafted.transfer,
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


FLOW = Flow(kind=KIND, app="quarantine", carry_out=make_transfer)


async def ask_to_move_blocked_stock(
    marg: Path, records: Path, godown: str, planned: Posting, *, out: Path
) -> ApprovalRequest:
    """Put a drafted quarantine transfer up for approval; returns the request made."""
    state = {
        "transfer": {"marg": str(marg), "godown": godown},
        "posting": asdict(planned),
        "out": str(out),
    }
    return await ask(KIND, state, records=records)
