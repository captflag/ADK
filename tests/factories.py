"""Small builders that keep tests focused on the behaviour under test."""

from datetime import date, datetime, timedelta, timezone
from itertools import count

from batchward.core.models import BatchKey, MovementType, StockMovement

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

_ids = count(1)


def at(day: int = 1, hour: int = 10, *, month: int = 1, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, tzinfo=IST)


def batch_key(
    batch_no: str = "AZ4021",
    *,
    company_id: str = "C01",
    item_id: str = "I001",
    expiry: date = date(2027, 10, 31),
) -> BatchKey:
    return BatchKey(company_id=company_id, item_id=item_id, batch_no=batch_no, expiry=expiry)


def movement(
    kind: MovementType,
    qty: int,
    *,
    when: datetime | None = None,
    batch: BatchKey | None = None,
    location_id: str = "GODOWN",
    party_id: str | None = None,
    movement_id: str | None = None,
    reverses: str | None = None,
) -> StockMovement:
    return StockMovement(
        id=movement_id or f"M{next(_ids)}",
        at=when or at(),
        kind=kind,
        batch=batch or batch_key(),
        location_id=location_id,
        qty=qty,
        document_ref="DOC-1",
        party_id=party_id,
        reverses=reverses,
    )
