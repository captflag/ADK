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

from batchward.bridge.marg_layout import (
    PARTY_TYPES,
    parse_date,
    parse_expiry,
    parse_gst,
    parse_money,
)
from batchward.core.models import Batch, BatchKey, Item, Party, Schedule


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
