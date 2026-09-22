"""The stock data the agents' tools read.

Tools are plain functions with no arguments for data, so ADK can describe them
to the model; they read the business through ``current()``. By default that
loads a Marg database named by ``BATCHWARD_MARG_DB`` (for example one written by
``batchward demo-marg``). Tests and scripts can install data directly with
``use()``.

Marg has no notion of a location that is never sold from, so the ids of such
locations come from ``BATCHWARD_UNSELLABLE_LOCATIONS`` (comma-separated,
default ``RETURNS``). "Today" is the day after the last recorded movement,
unless ``BATCHWARD_TODAY`` gives an ISO date.

Recall notices and holds are not in Marg; they are in Batchward's own records
database (ADR 0010), named by ``BATCHWARD_RECORDS``. It is optional: without it,
tools that need it say so.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from batchward.bridge.marg_contract import ensure_layout
from batchward.bridge.marg_import import read_ledger, read_masters
from batchward.core.clock import ist_date
from batchward.core.ledger import Ledger
from batchward.core.models import Batch, BatchKey, Item, Location, Party


class DataUnavailableError(RuntimeError):
    """No stock data is configured for the tools to read."""


@dataclass(frozen=True, slots=True)
class StockData:
    parties: dict[str, Party]
    items: dict[str, Item]
    batches: dict[BatchKey, Batch]
    ledger: Ledger
    locations: tuple[Location, ...]
    today: date


def load_marg(
    path: Path,
    *,
    today: date | None = None,
    unsellable: frozenset[str] = frozenset({"RETURNS"}),
) -> StockData:
    """Read a Marg-shaped database, checking its layout first (ADR 0006)."""
    if not path.is_file():
        raise DataUnavailableError(f"no Marg database at {path}")
    try:
        with closing(sqlite3.connect(path)) as connection:
            ensure_layout(connection)
            masters = read_masters(connection)
            ledger = read_ledger(connection, masters)
    except sqlite3.DatabaseError as error:
        raise DataUnavailableError(f"{path} is not a readable Marg database: {error}") from error
    earliest_today = _day_after_last_movement(ledger)
    if today is not None and today < earliest_today:
        raise ValueError(
            f"today ({today.isoformat()}) cannot be before the day after the last recorded "
            f"movement ({earliest_today.isoformat()}); tools would mix past and present stock"
        )
    location_ids = sorted({location_id for _, location_id in ledger.balances()} | set(unsellable))
    locations = tuple(
        Location(id=location_id, name=location_id, sellable=location_id not in unsellable)
        for location_id in location_ids
    )
    return StockData(
        parties=masters.parties,
        items=masters.items,
        batches=masters.batches,
        ledger=ledger,
        locations=locations,
        today=today or earliest_today,
    )


_installed: StockData | None = None


def current() -> StockData:
    """The data tools should read: installed data, or the configured Marg database."""
    global _installed
    if _installed is None:
        path = os.environ.get("BATCHWARD_MARG_DB")
        if not path:
            raise DataUnavailableError(
                "set BATCHWARD_MARG_DB to a Marg database, for example one written by "
                "`batchward demo-marg sim-out/marg.sqlite`"
            )
        today = os.environ.get("BATCHWARD_TODAY")
        unsellable = os.environ.get("BATCHWARD_UNSELLABLE_LOCATIONS", "RETURNS")
        _installed = load_marg(
            Path(path),
            today=date.fromisoformat(today) if today else None,
            unsellable=frozenset(s.strip() for s in unsellable.split(",") if s.strip()),
        )
    return _installed


_NOT_INSTALLED = object()
_installed_records: object = _NOT_INSTALLED


def records_path() -> Path | None:
    """Batchward's records database: the installed one, or ``BATCHWARD_RECORDS``, or None."""
    if isinstance(_installed_records, Path) or _installed_records is None:
        return _installed_records
    path = os.environ.get("BATCHWARD_RECORDS")
    return Path(path) if path else None


@contextmanager
def use(data: StockData, *, records: Path | str | None = None) -> Iterator[StockData]:
    """Install data, and optionally a records database, for the tools for a block."""
    global _installed, _installed_records
    previous = _installed, _installed_records
    _installed, _installed_records = data, None if records is None else Path(records)
    try:
        yield data
    finally:
        _installed, _installed_records = previous


def _day_after_last_movement(ledger: Ledger) -> date:
    moments = [m.at for m in ledger]
    if not moments:
        raise DataUnavailableError("the ledger has no movements to date the analysis from")
    return ist_date(max(moments)) + timedelta(days=1)
