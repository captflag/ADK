"""Where a recall stands: supplied against recovered, and the clock.

CDSCO expects a distributor to report the stock position of a recalled batch
and to reconcile what went out against what came back. This builds those
figures for one batch named in a notice, as they stood at any moment after it
arrived:

- the position when the notice arrived: units received, supplied to each
  chemist, still on hand, and any sold without a recorded buyer;
- what each chemist has returned since, and what is still outstanding;
- what happened to the batch since — returned to the company, written off, or
  (the thing that must never happen) sold after the notice;
- the deadlines, and whether each was met.

The recall counts as complete from the moment every unit in the market is back:
every unit each chemist held when the notice arrived or was sold since, and
none sold without a buyer. If a return is later reversed, or more is sold, it
stops being complete. Stock on hand is stopped by the block, and sale counts as
stopped from the start of the block still in force; a block lifted and not
placed again has not stopped it. Bills are judged as they stood at ``as_of``,
and a correction to a bill from before the notice changes what was supplied.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from batchward.compliance.recall import RecallNotice
from batchward.core.holds import HoldLog, Release
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, MovementType, StockMovement
from batchward.core.trace import effective_kind, trace_batch


class DeadlineState(StrEnum):
    MET = "met"
    MET_LATE = "met late"
    OPEN = "open"
    OVERDUE = "overdue"


@dataclass(frozen=True, slots=True)
class Deadline:
    name: str
    due: datetime
    done_at: datetime | None
    as_of: datetime

    @property
    def state(self) -> DeadlineState:
        if self.done_at is not None:
            return DeadlineState.MET if self.done_at <= self.due else DeadlineState.MET_LATE
        return DeadlineState.OVERDUE if self.as_of > self.due else DeadlineState.OPEN


@dataclass(frozen=True, slots=True)
class ChemistRecovery:
    party_id: str
    supplied: int
    """Units the chemist held from this batch when the notice arrived, as its bills now stand."""
    supplied_since: int
    """Units sold to the chemist after the notice arrived, net of reversals. They must come
    back too."""
    bills: tuple[str, ...]
    recovered: int
    """Units returned since the notice, net of any reversed returns."""
    last_recovered: datetime | None

    @property
    def due(self) -> int:
        """Every unit the chemist must send back."""
        return self.supplied + self.supplied_since

    @property
    def outstanding(self) -> int:
        return max(0, self.due - self.recovered)

    @property
    def excess(self) -> int:
        """Units returned beyond what the records say were supplied: a records problem."""
        return max(0, self.recovered - self.due)


@dataclass(frozen=True, slots=True)
class RecallReport:
    notice: RecallNotice
    batch: BatchKey
    as_of: datetime
    received: int
    """Units bought in before the notice, net of purchase returns."""
    on_hand_at_notice: dict[str, int]
    untraceable_at_notice: int
    """Units sold before the notice with no recorded buyer; they cannot be recalled by name."""
    untraceable: int
    """Units sold with no recorded buyer and not returned, as of ``as_of``."""
    chemists: tuple[ChemistRecovery, ...]
    """Largest outstanding first."""
    returned_to_company: int
    written_off: int
    in_transit: int
    """Units transferred out with no transfer in recorded: not on hand anywhere."""
    on_hand: dict[str, int]
    blocked_at: datetime | None
    """Start of the block in force at ``as_of``, or when the notice arrived if later."""
    releases: tuple[Release, ...]
    """Blocks on the batch lifted after the notice arrived."""
    sales_before_block: tuple[StockMovement, ...]
    """Sales after the notice arrived but before the batch was blocked."""
    sales_while_blocked: tuple[StockMovement, ...]
    sales_after_release: tuple[StockMovement, ...]
    """Sales after the notice arrived, once a block on the batch had been lifted."""
    stop_sale: Deadline | None
    completion: Deadline

    @property
    def supplied(self) -> int:
        """Units chemists held when the notice arrived."""
        return sum(c.supplied for c in self.chemists)

    @property
    def recovered(self) -> int:
        """Units recovered, counting no chemist beyond what it must send back."""
        return sum(min(c.recovered, c.due) for c in self.chemists)

    @property
    def outstanding(self) -> int:
        return sum(c.outstanding for c in self.chemists)

    @property
    def sold_after_notice(self) -> int:
        sales = (*self.sales_before_block, *self.sales_while_blocked, *self.sales_after_release)
        return -sum(m.qty for m in sales)


def recall_report(
    notice: RecallNotice,
    batch: BatchKey,
    *,
    ledger: Ledger,
    holds: HoldLog,
    as_of: datetime,
) -> RecallReport:
    """The recall of one batch as it stood at ``as_of``."""
    received_at = notice.received_at
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if as_of < received_at:
        raise ValueError("a recall report cannot be dated before its notice was received")

    at_notice = trace_batch(ledger, batch, as_of=received_at)
    now = trace_batch(ledger, batch, as_of=as_of)
    supplied: defaultdict[str, int] = defaultdict(int)
    bills: defaultdict[str, list[str]] = defaultdict(list)
    for recipient in at_notice.recipients:
        supplied[recipient.party_id] = recipient.units
        bills[recipient.party_id] = list(recipient.documents)
    since: defaultdict[str, int] = defaultdict(int)
    recovered: defaultdict[str, int] = defaultdict(int)
    last_recovered: dict[str, datetime] = {}
    untraceable = at_notice.untraceable

    def complete() -> bool:
        return untraceable <= 0 and all(
            supplied[party] + since[party] <= recovered[party]
            for party in supplied.keys() | since.keys()
        )

    blocked_at = _blocked_at(holds, batch, received_at, as_of)
    releases = _releases(holds, batch, received_at, as_of)
    returned_to_company = written_off = 0
    before_block: list[StockMovement] = []
    while_blocked: list[StockMovement] = []
    after_release: list[StockMovement] = []
    completed_at = received_at if complete() else None

    for m in ledger.movements_for(batch):
        if not received_at < m.at <= as_of:
            continue
        kind = effective_kind(ledger, m)
        if kind in (MovementType.SALE, MovementType.SALE_RETURN):
            corrects_earlier_bill = (
                m.reverses is not None and ledger.get(m.reverses).at <= received_at
            )
            if m.party_id is None:
                untraceable -= m.qty
            elif corrects_earlier_bill or kind is MovementType.SALE:
                target = supplied if corrects_earlier_bill else since
                target[m.party_id] -= m.qty
                if m.document_ref not in bills[m.party_id]:
                    bills[m.party_id].append(m.document_ref)
            else:
                recovered[m.party_id] += m.qty
                last_recovered[m.party_id] = m.at
        elif kind is MovementType.PURCHASE_RETURN:
            returned_to_company -= m.qty
        elif kind is MovementType.WRITE_OFF:
            written_off -= m.qty

        if m.kind is MovementType.SALE and not ledger.is_reversed(m.id, as_of):
            if holds.active(batch, m.at):
                while_blocked.append(m)
            elif any(release.at <= m.at for release in releases):
                after_release.append(m)
            else:
                before_block.append(m)

        if not complete():
            completed_at = None
        elif completed_at is None:
            completed_at = m.at

    chemists = [
        ChemistRecovery(
            party_id=party,
            supplied=supplied[party],
            supplied_since=since[party],
            bills=tuple(bills[party]),
            recovered=recovered[party],
            last_recovered=last_recovered.get(party),
        )
        for party in supplied.keys() | since.keys() | recovered.keys()
        if supplied[party] or since[party] or recovered[party]
    ]
    chemists.sort(key=lambda c: (-c.outstanding, -c.supplied, c.party_id))

    stop_sale_limit = notice.clock.stop_sale
    return RecallReport(
        notice=notice,
        batch=batch,
        as_of=as_of,
        received=at_notice.received,
        on_hand_at_notice=at_notice.on_hand,
        untraceable_at_notice=at_notice.untraceable,
        untraceable=max(0, untraceable),
        chemists=tuple(chemists),
        returned_to_company=returned_to_company,
        written_off=written_off,
        in_transit=now.in_transit,
        on_hand=now.on_hand,
        blocked_at=blocked_at,
        releases=releases,
        sales_before_block=tuple(before_block),
        sales_while_blocked=tuple(while_blocked),
        sales_after_release=tuple(after_release),
        stop_sale=None
        if stop_sale_limit is None
        else Deadline("stop sale", received_at + stop_sale_limit, blocked_at, as_of),
        completion=Deadline(
            "complete the recall", received_at + notice.clock.complete, completed_at, as_of
        ),
    )


def _blocked_at(
    holds: HoldLog, batch: BatchKey, received_at: datetime, as_of: datetime
) -> datetime | None:
    """When sale of the batch stopped for good as of ``as_of``: the start of the unbroken run of
    holds in force then, or the notice's arrival if the run began before it. None if no hold is
    in force at ``as_of``."""
    if not holds.active(batch, as_of):
        return None
    periods = [
        (hold.at, None if release is None else release.at)
        for hold in holds.holds_for(batch)
        if hold.at <= as_of
        for release in (holds.release_of(hold.id),)
    ]
    start, moved = as_of, True
    while moved:
        moved = False
        for placed, lifted in periods:
            if placed < start and (lifted is None or lifted >= start):
                start, moved = placed, True
    return max(start, received_at)


def _releases(
    holds: HoldLog, batch: BatchKey, received_at: datetime, as_of: datetime
) -> tuple[Release, ...]:
    lifted = (holds.release_of(hold.id) for hold in holds.holds_for(batch))
    return tuple(
        sorted(
            (r for r in lifted if r is not None and received_at < r.at <= as_of),
            key=lambda r: (r.at, r.hold_id),
        )
    )
