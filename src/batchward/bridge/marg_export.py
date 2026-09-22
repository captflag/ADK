"""Write stock data into Marg-shaped tables.

This gives the bridge a realistic database to read before a real Marg
installation is available. One thing cannot be written: a reversal. Marg
corrects a bill by editing or deleting it, whereas the ledger appends a
reversing entry, so turning edited bills into reversals is the job of the
bridge's change detection, not of this exporter.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping

from batchward.bridge.marg_layout import (
    PARTY_TYPES,
    TABLES,
    TIMEZONE,
    VOUCHER_TYPES,
    create_schema,
    format_date,
    format_expiry,
    format_gst,
    format_time,
)
from batchward.core.ledger import Ledger
from batchward.core.models import Batch, BatchKey, Item, MovementType, Party, StockMovement

_PARTY_CODES = {kind: code for code, kind in PARTY_TYPES.items()}
_VOUCHER_CODES = {(kind, sign): code for code, (kind, sign) in VOUCHER_TYPES.items()}


def export_to_marg(
    connection: sqlite3.Connection,
    *,
    parties: Iterable[Party],
    items: Iterable[Item],
    batches: Mapping[BatchKey, Batch],
    ledger: Ledger,
) -> None:
    """Create the Marg tables and fill them. The database must be empty.

    Every row is built before anything is written, so data Marg cannot hold is
    refused with a ``ValueError`` and leaves the database untouched.
    """
    movements = list(ledger)
    for m in movements:
        if m.kind is MovementType.REVERSAL:
            raise ValueError(
                f"movement {m.id} is a reversal; Marg records corrections as edited bills"
            )
        if m.batch not in batches:
            raise ValueError(f"movement {m.id} refers to batch {m.batch} with no batch record")

    party_rows = [
        (p.id, p.name, _PARTY_CODES[p.kind], p.drug_licence_no, p.gstin, p.address) for p in parties
    ]
    item_rows = [
        (
            i.id,
            i.brand,
            i.company_id,
            i.molecule,
            i.strength,
            i.unit,
            i.hsn,
            format_gst(i.gst_rate),
            float(i.mrp),
            ",".join(sorted(i.schedules)),
            int(i.dpco_scheduled),
            int(i.cold_chain),
        )
        for i in items
    ]

    stock: defaultdict[BatchKey, int] = defaultdict(int)
    for (key, _location), qty in ledger.balances().items():
        stock[key] += qty
    batch_rows = [
        (
            key.item_id,
            key.batch_no,
            format_expiry(key.expiry),
            format_date(batch.manufactured),
            float(batch.mrp),
            stock[key],
        )
        for key, batch in batches.items()
    ]

    line_numbers: defaultdict[str, int] = defaultdict(int)
    bill_rows = []
    for m in movements:
        line_numbers[m.document_ref] += 1
        bill_rows.append(_voucher_line(m, line_numbers[m.document_ref]))

    create_schema(connection)
    _insert(connection, "ORDER", party_rows)
    _insert(connection, "PRO", item_rows)
    _insert(connection, "PROBAT", batch_rows)
    _insert(connection, "DIS", bill_rows)
    connection.commit()


def _voucher_line(m: StockMovement, line: int) -> tuple:
    local = m.at.astimezone(TIMEZONE)
    return (
        m.document_ref,
        line,
        _VOUCHER_CODES[(m.kind, 1 if m.qty > 0 else -1)],
        format_date(local.date()),
        format_time(local.time()),
        m.party_id,
        m.batch.item_id,
        m.batch.batch_no,
        format_expiry(m.batch.expiry),
        abs(m.qty),
        None if m.rate is None else float(m.rate),
        m.location_id,
    )


def _insert(connection: sqlite3.Connection, table: str, rows: list[tuple]) -> None:
    placeholders = ", ".join("?" for _ in TABLES[table])
    connection.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})', rows)
