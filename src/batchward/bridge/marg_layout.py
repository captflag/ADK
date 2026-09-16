"""The Marg ERP table layout the bridge expects (ADR 0006).

The table names come from Marg's own help pages: ``ORDER`` holds ledger
(party) records, ``PRO`` the item master, ``PROBAT`` batches with their stock,
and ``DIS`` bill line items. Marg publishes no column reference, so **every
column here is an assumption** made for the mock database. When a real Marg
installation is available, capture its layout and update this module; the
contract check will then flag every place the bridge disagrees.

Values are stored the way Indian billing software presents them — GST as a
percentage, expiry as ``MM/YYYY``, dates as ``DD/MM/YYYY``, quantities always
positive with the direction carried by the voucher type — so the bridge has
real conversions to get right.
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date, datetime, time
from decimal import Decimal

from batchward.core.clock import IST
from batchward.core.models import MovementType, PartyKind

TIMEZONE = IST
"""Marg stores local dates and times with no zone; they are Indian Standard Time."""

TABLES: dict[str, dict[str, str]] = {
    "ORDER": {
        "CODE": "TEXT",
        "NAME": "TEXT",
        "TYPE": "TEXT",
        "DLNO": "TEXT",
        "GSTIN": "TEXT",
    },
    "PRO": {
        "CODE": "TEXT",
        "NAME": "TEXT",
        "COMPANY": "TEXT",
        "SALT": "TEXT",
        "STRENGTH": "TEXT",
        "PACK": "TEXT",
        "HSN": "TEXT",
        "GST": "REAL",
        "MRP": "REAL",
        "SCHEDULE": "TEXT",
        "DPCO": "INTEGER",
        "COLD": "INTEGER",
    },
    "PROBAT": {
        "PCODE": "TEXT",
        "BATCH": "TEXT",
        "EXPIRY": "TEXT",
        "MFG": "TEXT",
        "MRP": "REAL",
        "STOCK": "INTEGER",
    },
    "DIS": {
        "VNO": "TEXT",
        "LINE": "INTEGER",
        "VTYPE": "TEXT",
        "VDATE": "TEXT",
        "VTIME": "TEXT",
        "PARTY": "TEXT",
        "PCODE": "TEXT",
        "BATCH": "TEXT",
        "EXPIRY": "TEXT",
        "QTY": "INTEGER",
        "RATE": "REAL",
        "GODOWN": "TEXT",
    },
}

_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "ORDER": ("CODE",),
    "PRO": ("CODE",),
    "PROBAT": ("PCODE", "BATCH", "EXPIRY"),
    "DIS": ("VNO", "LINE"),
}

PARTY_TYPES: dict[str, PartyKind] = {
    "CO": PartyKind.COMPANY,
    "CH": PartyKind.CHEMIST,
    "NH": PartyKind.NURSING_HOME,
}

VOUCHER_TYPES: dict[str, tuple[MovementType, int]] = {
    "P": (MovementType.PURCHASE, +1),
    "PR": (MovementType.PURCHASE_RETURN, -1),
    "S": (MovementType.SALE, -1),
    "SR": (MovementType.SALE_RETURN, +1),
    "TI": (MovementType.TRANSFER_IN, +1),
    "TO": (MovementType.TRANSFER_OUT, -1),
    "BE": (MovementType.WRITE_OFF, -1),
    "AI": (MovementType.ADJUSTMENT, +1),
    "AO": (MovementType.ADJUSTMENT, -1),
}
"""Voucher type code → (movement kind, sign applied to the positive quantity)."""


def create_schema(connection: sqlite3.Connection) -> None:
    for table, columns in TABLES.items():
        column_sql = ", ".join(f'"{name}" {kind}' for name, kind in columns.items())
        key_sql = ", ".join(f'"{name}"' for name in _PRIMARY_KEYS[table])
        connection.execute(f'CREATE TABLE "{table}" ({column_sql}, PRIMARY KEY ({key_sql}))')


def format_date(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def parse_date(text: str) -> date:
    return datetime.strptime(text, "%d/%m/%Y").date()


def format_time(clock: time) -> str:
    return clock.strftime("%H:%M")


def parse_time(text: str) -> time:
    return datetime.strptime(text, "%H:%M").time()


def format_expiry(expiry: date) -> str:
    """Expiry as printed on the pack. Only month-end dates can be represented."""
    if expiry.day != calendar.monthrange(expiry.year, expiry.month)[1]:
        raise ValueError(f"Marg stores expiry as MM/YYYY; {expiry.isoformat()} is not a month end")
    return expiry.strftime("%m/%Y")


def parse_expiry(text: str) -> date:
    """``MM/YYYY`` means usable until the end of that month."""
    month, year = (int(part) for part in text.split("/"))
    return date(year, month, calendar.monthrange(year, month)[1])


def format_gst(rate: Decimal) -> float:
    """A domain fraction (0.05) as Marg's percentage (5.0)."""
    return float(rate * 100)


def parse_gst(percent: float) -> Decimal:
    return (Decimal(str(percent)) / 100).normalize()


def parse_money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))
