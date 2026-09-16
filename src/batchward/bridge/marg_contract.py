"""Check a Marg database against the layout the bridge expects (ADR 0006).

Run before every sync. If a Marg upgrade drops, renames or retypes a column
the bridge relies on, the sync stops with a readable report instead of
importing data of unknown shape. Extra columns are tolerated: an addition
cannot break what the bridge reads.

Types are compared as SQLite type names, which is what the mock uses. A real
installation reached over ODBC reports SQL Server types, which will need
mapping onto these before comparison.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from batchward.bridge.marg_layout import TABLES


@dataclass(frozen=True, slots=True)
class LayoutMismatch:
    table: str
    column: str | None
    problem: str

    def __str__(self) -> str:
        where = self.table if self.column is None else f"{self.table}.{self.column}"
        return f"{where}: {self.problem}"


class MargLayoutError(RuntimeError):
    def __init__(self, mismatches: list[LayoutMismatch]) -> None:
        self.mismatches = mismatches
        details = "\n".join(f"  - {m}" for m in mismatches)
        super().__init__(
            f"Marg's tables do not match the layout the bridge expects; sync stopped.\n{details}"
        )


def check_layout(
    connection: sqlite3.Connection,
    expected: Mapping[str, Mapping[str, str]] = TABLES,
) -> list[LayoutMismatch]:
    """Every way the database differs from the expected layout. Empty means it matches."""
    mismatches = []
    for table, columns in expected.items():
        found = {
            name.upper(): declared.upper()
            for _, name, declared, *_ in connection.execute(f'PRAGMA table_info("{table}")')
        }
        if not found:
            mismatches.append(LayoutMismatch(table, None, "table is missing"))
            continue
        for column, kind in columns.items():
            if column not in found:
                mismatches.append(LayoutMismatch(table, column, "column is missing"))
            elif found[column] != kind:
                actual = found[column] or "no declared type"
                mismatches.append(LayoutMismatch(table, column, f"expected {kind}, found {actual}"))
    return mismatches


def ensure_layout(connection: sqlite3.Connection) -> None:
    """Raise ``MargLayoutError`` listing every mismatch, if there are any."""
    mismatches = check_layout(connection)
    if mismatches:
        raise MargLayoutError(mismatches)
