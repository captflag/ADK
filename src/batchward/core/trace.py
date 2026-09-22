"""Where a batch went: the foundation of a recall (ADR 0004).

A trace answers the questions a Drugs Inspector asks when a batch is recalled:
how much was received, who it was supplied to, and how much is still on hand.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, MovementType, StockMovement

_SUPPLY = {MovementType.SALE, MovementType.SALE_RETURN}
_RECEIPT = {MovementType.PURCHASE, MovementType.PURCHASE_RETURN}
_TRANSFER = {MovementType.TRANSFER_IN, MovementType.TRANSFER_OUT}


@dataclass(frozen=True, slots=True)
class Recipient:
    party_id: str
    units: int
    """Units supplied, net of sale returns and reversals."""
    first_supplied: datetime
    last_supplied: datetime
    documents: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BatchTrace:
    batch: BatchKey
    received: int
    """Units bought in, net of purchase returns. Internal transfers are not receipts."""
    recipients: tuple[Recipient, ...]
    """Parties still holding supplied units, largest first."""
    untraceable: int
    """Units sold with no recorded buyer. Any non-zero value needs a person."""
    written_off: int
    adjusted: int
    """Net manual adjustments, signed."""
    in_transit: int
    """Units transferred out of one location and not yet recorded arriving at another."""
    on_hand: dict[str, int]
    """Units remaining at each location."""

    @property
    def supplied(self) -> int:
        return sum(r.units for r in self.recipients)


def trace_batch(ledger: Ledger, batch: BatchKey, as_of: datetime | None = None) -> BatchTrace:
    """Trace a batch through the ledger, optionally as it stood at ``as_of``."""
    received = untraceable = written_off = adjusted = in_transit = 0
    units: defaultdict[str, int] = defaultdict(int)
    supplied_at: defaultdict[str, list[datetime]] = defaultdict(list)
    documents: defaultdict[str, list[str]] = defaultdict(list)
    on_hand: defaultdict[str, int] = defaultdict(int)

    for m in ledger.movements_for(batch):
        if as_of is not None and m.at > as_of:
            continue
        on_hand[m.location_id] += m.qty
        kind = effective_kind(ledger, m)
        if kind in _RECEIPT:
            received += m.qty
        elif kind in _SUPPLY:
            if m.party_id is None:
                untraceable -= m.qty
                continue
            units[m.party_id] -= m.qty
            if kind is MovementType.SALE and m.kind is MovementType.SALE:
                supplied_at[m.party_id].append(m.at)
            if m.document_ref not in documents[m.party_id]:
                documents[m.party_id].append(m.document_ref)
        elif kind is MovementType.WRITE_OFF:
            written_off -= m.qty
        elif kind is MovementType.ADJUSTMENT:
            adjusted += m.qty
        elif kind in _TRANSFER:
            in_transit -= m.qty

    recipients = sorted(
        (
            Recipient(
                party_id=party_id,
                units=qty,
                first_supplied=min(supplied_at[party_id]),
                last_supplied=max(supplied_at[party_id]),
                documents=tuple(documents[party_id]),
            )
            for party_id, qty in units.items()
            if qty > 0
        ),
        key=lambda r: (-r.units, r.party_id),
    )
    return BatchTrace(
        batch=batch,
        received=received,
        recipients=tuple(recipients),
        untraceable=untraceable,
        written_off=written_off,
        adjusted=adjusted,
        in_transit=in_transit,
        on_hand={location: qty for location, qty in on_hand.items() if qty},
    )


def effective_kind(ledger: Ledger, movement: StockMovement) -> MovementType:
    """A reversal counts as the opposite of the kind of movement it reverses."""
    if movement.reverses is None:
        return movement.kind
    return ledger.get(movement.reverses).kind
