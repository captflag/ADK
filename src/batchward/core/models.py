"""Domain models for a pharma distributor's stock.

Quantities are integers in the smallest unit the distributor sells — usually a
strip, vial or bottle — never in packs of those units. Money is ``Decimal``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class Schedule(StrEnum):
    """Drug schedules that add record-keeping duties for a wholesaler."""

    H = "H"
    H1 = "H1"
    X = "X"


class BatchStatus(StrEnum):
    LIVE = "live"
    QUARANTINED = "quarantined"
    BLOCKED = "blocked"
    RECALLED = "recalled"


class PartyKind(StrEnum):
    COMPANY = "company"
    CHEMIST = "chemist"
    NURSING_HOME = "nursing_home"


class MovementType(StrEnum):
    PURCHASE = "purchase"
    SALE = "sale"
    SALE_RETURN = "sale_return"
    PURCHASE_RETURN = "purchase_return"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    ADJUSTMENT = "adjustment"
    WRITE_OFF = "write_off"
    REVERSAL = "reversal"


INBOUND: frozenset[MovementType] = frozenset(
    {MovementType.PURCHASE, MovementType.SALE_RETURN, MovementType.TRANSFER_IN}
)
OUTBOUND: frozenset[MovementType] = frozenset(
    {
        MovementType.SALE,
        MovementType.PURCHASE_RETURN,
        MovementType.TRANSFER_OUT,
        MovementType.WRITE_OFF,
    }
)


@dataclass(frozen=True, slots=True, order=True)
class BatchKey:
    """The identity of a batch (ADR 0002).

    Batch numbers are normalised for whitespace and letter case only. Look-alike
    characters such as ``0`` and ``O`` are deliberately left distinct.
    """

    company_id: str
    item_id: str
    batch_no: str
    expiry: date

    def __post_init__(self) -> None:
        normalised = "".join(self.batch_no.split()).upper()
        if not normalised:
            raise ValueError("batch_no must not be blank")
        object.__setattr__(self, "batch_no", normalised)


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    company_id: str
    brand: str
    molecule: str
    strength: str
    unit: str
    """The smallest unit sold, e.g. ``"strip of 10 tablets"``."""
    hsn: str
    gst_rate: Decimal
    """A fraction: ``Decimal("0.05")`` for 5%."""
    mrp: Decimal
    """Maximum retail price per unit, including GST."""
    schedules: frozenset[Schedule] = frozenset()
    dpco_scheduled: bool = False
    cold_chain: bool = False

    def __post_init__(self) -> None:
        if not Decimal(0) <= self.gst_rate < Decimal(1):
            raise ValueError("gst_rate must be a fraction, e.g. Decimal('0.05')")
        if self.mrp <= 0:
            raise ValueError("mrp must be positive")


@dataclass(frozen=True, slots=True)
class Batch:
    key: BatchKey
    manufactured: date
    mrp: Decimal
    status: BatchStatus = BatchStatus.LIVE

    def __post_init__(self) -> None:
        if self.manufactured >= self.key.expiry:
            raise ValueError("a batch must be manufactured before it expires")
        if self.mrp <= 0:
            raise ValueError("mrp must be positive")

    def is_sellable(self, on: date) -> bool:
        """Live and not yet expired. Stock is not sold on its expiry date."""
        return self.status is BatchStatus.LIVE and on < self.key.expiry


@dataclass(frozen=True, slots=True)
class Party:
    id: str
    kind: PartyKind
    name: str
    drug_licence_no: str | None = None
    gstin: str | None = None

    def __post_init__(self) -> None:
        # Rule 65: a wholesale memo must carry the buyer's drug licence number.
        if self.kind is PartyKind.CHEMIST and not self.drug_licence_no:
            raise ValueError("a chemist must have a drug licence number")


@dataclass(frozen=True, slots=True)
class Location:
    id: str
    name: str
    cold_room: bool = False
    sellable: bool = True
    """False for places stock waits but is never sold from, such as a breakage and expiry shelf."""


@dataclass(frozen=True, slots=True)
class StockMovement:
    """One immutable change to stock (ADR 0001).

    ``qty`` is signed: positive adds stock at the location, negative removes it.
    """

    id: str
    at: datetime
    kind: MovementType
    batch: BatchKey
    location_id: str
    qty: int
    document_ref: str
    party_id: str | None = None
    rate: Decimal | None = None
    reverses: str | None = None

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        if self.qty == 0:
            raise ValueError("qty must not be zero")
        if self.kind in INBOUND and self.qty < 0:
            raise ValueError(f"a {self.kind} movement must add stock")
        if self.kind in OUTBOUND and self.qty > 0:
            raise ValueError(f"a {self.kind} movement must remove stock")
        if (self.kind is MovementType.REVERSAL) != (self.reverses is not None):
            raise ValueError("reverses is required for a reversal, and only for a reversal")
