"""Read Marg-shaped tables back into the domain model.

This is the read side of the bridge. Against a real installation the
connection is ODBC; the mock uses SQLite with the same layout. Rows that do not
fit the domain — an unknown party type, a batch for an item that does not
exist — raise ``MargDataError`` naming the offending row, because importing
them silently would corrupt the ledger.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from batchward.bridge.marg_layout import (
    PARTY_TYPES,
    TIMEZONE,
    VOUCHER_TYPES,
    parse_date,
    parse_expiry,
    parse_gst,
    parse_money,
    parse_time,
)
from batchward.core.ledger import Ledger
from batchward.core.models import Batch, BatchKey, Item, Party, Schedule, StockMovement


class MargDataError(ValueError):
    """A row in Marg that cannot be turned into a valid domain record."""


@dataclass(frozen=True, slots=True)
class MargMasters:
    parties: dict[str, Party]
    items: dict[str, Item]
    batches: dict[BatchKey, Batch]
    stock: dict[BatchKey, int]
    """Stock per batch as Marg itself reports it, kept for reconciliation."""


def read_masters(connection: sqlite3.Connection) -> MargMasters:
    parties = {}
    for code, name, type_code, licence, gstin in connection.execute(
        'SELECT CODE, NAME, TYPE, DLNO, GSTIN FROM "ORDER" ORDER BY CODE'
    ):
        if type_code not in PARTY_TYPES:
            raise MargDataError(f"party {code} has unknown type {type_code!r}")
        parties[code] = Party(
            id=code,
            kind=PARTY_TYPES[type_code],
            name=name,
            drug_licence_no=licence,
            gstin=gstin,
        )

    items = {}
    for row in connection.execute(
        "SELECT CODE, NAME, COMPANY, SALT, STRENGTH, PACK, HSN, GST, MRP, SCHEDULE, DPCO, COLD "
        'FROM "PRO" ORDER BY CODE'
    ):
        code, brand, company, salt, strength, pack, hsn, gst, mrp, schedule, dpco, cold = row
        if company not in parties:
            raise MargDataError(f"item {code} belongs to unknown company {company!r}")
        items[code] = Item(
            id=code,
            company_id=company,
            brand=brand,
            molecule=salt,
            strength=strength,
            unit=pack,
            hsn=hsn,
            gst_rate=parse_gst(gst),
            mrp=parse_money(mrp),
            schedules=frozenset(Schedule(s) for s in schedule.split(",") if s),
            dpco_scheduled=bool(dpco),
            cold_chain=bool(cold),
        )

    batches = {}
    stock = {}
    for item_code, batch_no, expiry, manufactured, mrp, qty in connection.execute(
        'SELECT PCODE, BATCH, EXPIRY, MFG, MRP, STOCK FROM "PROBAT" ORDER BY PCODE, BATCH'
    ):
        item = items.get(item_code)
        if item is None:
            raise MargDataError(f"batch {batch_no} refers to unknown item {item_code!r}")
        key = BatchKey(
            company_id=item.company_id,
            item_id=item_code,
            batch_no=batch_no,
            expiry=parse_expiry(expiry),
        )
        batches[key] = Batch(key=key, manufactured=parse_date(manufactured), mrp=parse_money(mrp))
        stock[key] = qty

    return MargMasters(parties=parties, items=items, batches=batches, stock=stock)


def read_ledger(connection: sqlite3.Connection, masters: MargMasters) -> Ledger:
    """Replay Marg's bill lines into a ledger.

    Lines are replayed in time order. Marg bills are often dated but not timed,
    so many lines share an instant; within one instant, stock coming in is
    replayed before stock going out, then by bill and line number.
    """
    replay: list[tuple[tuple, StockMovement]] = []
    for row in connection.execute(
        "SELECT VNO, LINE, VTYPE, VDATE, VTIME, PARTY, PCODE, BATCH, EXPIRY, QTY, RATE, GODOWN "
        'FROM "DIS"'
    ):
        vno, line, vtype, vdate, vtime, party, item_code, batch_no, expiry, qty, rate, godown = row
        where = f"bill {vno} line {line}"
        if vtype not in VOUCHER_TYPES:
            raise MargDataError(f"{where} has unknown voucher type {vtype!r}")
        if qty <= 0:
            raise MargDataError(f"{where} has quantity {qty}; Marg quantities are positive")
        item = masters.items.get(item_code)
        if item is None:
            raise MargDataError(f"{where} refers to unknown item {item_code!r}")
        key = BatchKey(
            company_id=item.company_id,
            item_id=item_code,
            batch_no=batch_no,
            expiry=parse_expiry(expiry),
        )
        if key not in masters.batches:
            raise MargDataError(f"{where} refers to batch {batch_no} ({expiry}) with no record")
        if party is not None and party not in masters.parties:
            raise MargDataError(f"{where} refers to unknown party {party!r}")

        kind, sign = VOUCHER_TYPES[vtype]
        at = datetime.combine(parse_date(vdate), parse_time(vtime), tzinfo=TIMEZONE)
        movement = StockMovement(
            id=f"MARG:{vno}:{line}",
            at=at,
            kind=kind,
            batch=key,
            location_id=godown,
            qty=sign * qty,
            document_ref=vno,
            party_id=party,
            rate=None if rate is None else parse_money(rate),
        )
        replay.append(((at, sign < 0, vno, line), movement))

    replay.sort(key=lambda entry: entry[0])
    return Ledger(movement for _, movement in replay)
