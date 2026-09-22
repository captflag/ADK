"""Recalls that persist: receiving a notice, lifting a block, and reporting later.

The recall engine (``compliance.recall``) decides what to block; this module
makes those decisions durable. Receiving a notice records it and blocks the
batches it names exactly in one transaction, so a crash can never leave a
recorded notice without its block, or a block without the notice behind it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from batchward.compliance.recall import (
    Candidate,
    Recall,
    RecallNotice,
    batches_ever_held,
    match_notice,
    open_recall,
)
from batchward.compliance.recall_report import RecallReport, recall_report
from batchward.core.holds import Hold, Release
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, Item, Party
from batchward.records.store import RecordStore


def receive_notice(
    store: RecordStore,
    notice: RecallNotice,
    *,
    ledger: Ledger,
    items: Mapping[str, Item],
    parties: Mapping[str, Party],
    at: datetime,
    batches: Iterable[BatchKey] = (),
) -> Recall:
    """Record a recall notice and block every batch it names exactly (ADR 0004).

    ``at`` is when this happens; the blocks are dated then. Receiving the same
    notice again records nothing new, places no second block, and leaves alone a
    block a person has released.
    """
    with store.transaction():
        store.save_notice(notice)
        holds = store.hold_log()
        recall = open_recall(
            notice,
            ledger=ledger,
            items=items,
            parties=parties,
            holds=holds,
            at=at,
            batches=batches,
        )
        for hold in recall.holds:
            store.save_hold(hold)
    return recall


def release_hold(
    store: RecordStore, hold_id: str, *, at: datetime, reason: str, released_by: str
) -> Release:
    """Lift a hold. The hold stays on record; the release is recorded beside it."""
    with store.transaction():
        release = store.hold_log().release(hold_id, at=at, reason=reason, released_by=released_by)
        store.save_release(release)
    return release


@dataclass(frozen=True, slots=True)
class RecallStatus:
    notice: RecallNotice
    holds: tuple[Hold, ...]
    """Holds placed for this notice, released or not."""
    reports: tuple[RecallReport, ...]
    """One per batch the notice blocked."""
    unblocked: tuple[BatchKey, ...]
    """Batches the notice names exactly that it never blocked, such as stock that arrived
    after the notice was received. Receiving the notice again blocks them."""
    review: tuple[Candidate, ...]
    """Batches the notice resembles but did not block, from matching it again now."""


def recall_status(
    store: RecordStore,
    reference: str,
    *,
    ledger: Ledger,
    items: Mapping[str, Item],
    parties: Mapping[str, Party],
    as_of: datetime,
    batches: Iterable[BatchKey] = (),
) -> RecallStatus:
    """Where a recorded recall stands at ``as_of``, which may not precede its notice."""
    notice = store.notice(reference)
    if notice is None:
        raise LookupError(f"no recall notice recorded under reference {reference}")
    holds = store.hold_log()
    placed = tuple(hold for hold in holds if hold.reference == reference)
    batches = sorted({hold.batch for hold in placed})
    reports = tuple(
        recall_report(notice, batch, ledger=ledger, holds=holds, as_of=as_of) for batch in batches
    )
    candidates = batches_ever_held(ledger) | set(batches)
    match = match_notice(notice, candidates, items=items, parties=parties)
    blocked = {hold.batch for hold in placed}
    return RecallStatus(
        notice=notice,
        holds=placed,
        reports=reports,
        unblocked=tuple(key for key in match.exact if key not in blocked),
        review=match.review,
    )
