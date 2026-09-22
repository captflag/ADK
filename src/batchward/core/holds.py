"""Holds that stop a batch being sold, and their release.

A hold changes whether a batch may be sold, never how much of it there is. Like
stock movements, holds are never edited (ADR 0001): placing one appends a
record and lifting it appends a release. So the status of any batch at any
moment can be answered afterwards — exactly what a Drugs Inspector asks once a
recall is over — and a block placed in error is undone visibly, not erased.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from batchward.core.models import Batch, BatchKey, BatchStatus

SEVERITY: dict[BatchStatus, int] = {
    BatchStatus.LIVE: 0,
    BatchStatus.QUARANTINED: 1,
    BatchStatus.BLOCKED: 2,
    BatchStatus.RECALLED: 3,
}
"""When several apply, the most severe status wins."""


class HoldError(Exception):
    """A hold or release that cannot be recorded."""


@dataclass(frozen=True, slots=True)
class Hold:
    id: str
    batch: BatchKey
    status: BatchStatus
    at: datetime
    reason: str
    reference: str
    """The notice, report or document behind the hold."""
    placed_by: str
    """``"system"`` for automatic holds (ADR 0004), otherwise the person who placed it."""

    def __post_init__(self) -> None:
        if self.status is BatchStatus.LIVE:
            raise HoldError("a hold must quarantine, block or recall a batch, not make it live")
        if self.at.tzinfo is None:
            raise HoldError("at must be timezone-aware")
        if not self.reason.strip() or not self.placed_by.strip():
            raise HoldError("a hold needs a reason and who placed it")


@dataclass(frozen=True, slots=True)
class Release:
    hold_id: str
    at: datetime
    reason: str
    released_by: str

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise HoldError("at must be timezone-aware")
        if not self.reason.strip() or not self.released_by.strip():
            raise HoldError("a release needs a reason and who released the hold")


class HoldLog:
    """Every hold ever placed and every release, in the order they were recorded."""

    def __init__(self) -> None:
        self._holds: dict[str, Hold] = {}
        self._releases: dict[str, Release] = {}
        self._by_batch: defaultdict[BatchKey, list[Hold]] = defaultdict(list)

    def __iter__(self) -> Iterator[Hold]:
        return iter(self._holds.values())

    def __len__(self) -> int:
        return len(self._holds)

    def place(
        self,
        batch: BatchKey,
        status: BatchStatus,
        *,
        at: datetime,
        reason: str,
        reference: str,
        placed_by: str,
    ) -> Hold:
        number = len(self._holds) + 1
        while f"H{number:05d}" in self._holds:
            number += 1
        return self.record(
            Hold(
                id=f"H{number:05d}",
                batch=batch,
                status=status,
                at=at,
                reason=reason,
                reference=reference,
                placed_by=placed_by,
            )
        )

    def record(self, hold: Hold) -> Hold:
        """Add a hold placed earlier, such as one read back from storage."""
        if hold.id in self._holds:
            raise HoldError(f"a hold with id {hold.id} is already recorded")
        self._holds[hold.id] = hold
        self._by_batch[hold.batch].append(hold)
        return hold

    def release(self, hold_id: str, *, at: datetime, reason: str, released_by: str) -> Release:
        return self.record_release(
            Release(hold_id=hold_id, at=at, reason=reason, released_by=released_by)
        )

    def record_release(self, release: Release) -> Release:
        """Add a release, checked against the hold it lifts."""
        hold = self._holds.get(release.hold_id)
        if hold is None:
            raise HoldError(f"no hold with id {release.hold_id!r}")
        if release.hold_id in self._releases:
            raise HoldError(f"hold {release.hold_id} was already released")
        if release.at < hold.at:
            raise HoldError(f"hold {release.hold_id} cannot be released before it was placed")
        self._releases[release.hold_id] = release
        return release

    def get(self, hold_id: str) -> Hold:
        hold = self._holds.get(hold_id)
        if hold is None:
            raise HoldError(f"no hold with id {hold_id!r}")
        return hold

    def release_of(self, hold_id: str) -> Release | None:
        return self._releases.get(hold_id)

    def holds_for(self, batch: BatchKey) -> list[Hold]:
        """Every hold ever placed on a batch, released or not, earliest first."""
        return sorted(self._by_batch.get(batch, []), key=lambda h: (h.at, h.id))

    def active(self, batch: BatchKey, at: datetime | None = None) -> list[Hold]:
        """Holds in force on a batch at a moment.

        Without ``at``, every recorded hold and release counts as having taken effect,
        including one dated later than the real time; callers recording a hold or
        release refuse times in the future so that this is the present.
        """
        return [hold for hold in self.holds_for(batch) if self._in_force(hold, at)]

    def status(self, batch: Batch, at: datetime | None = None) -> BatchStatus:
        """The batch's own status, overridden by any more severe hold in force."""
        statuses = [batch.status, *(hold.status for hold in self.active(batch.key, at))]
        return max(statuses, key=SEVERITY.__getitem__)

    def apply(
        self, batches: Mapping[BatchKey, Batch], at: datetime | None = None
    ) -> dict[BatchKey, Batch]:
        """Batch records as they stand once holds in force are taken into account.

        Pass the result to first-expiry-first-out allocation so held stock is never picked.
        """
        applied = dict(batches)
        for key in self._by_batch:
            batch = applied.get(key)
            if batch is None:
                continue
            status = self.status(batch, at)
            if status is not batch.status:
                applied[key] = replace(batch, status=status)
        return applied

    def _in_force(self, hold: Hold, at: datetime | None) -> bool:
        if at is not None and hold.at > at:
            return False
        release = self._releases.get(hold.id)
        return release is None or (at is not None and release.at > at)
