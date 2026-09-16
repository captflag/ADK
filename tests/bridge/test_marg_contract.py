import pytest

from batchward.bridge.marg_contract import (
    LayoutMismatch,
    MargLayoutError,
    check_layout,
    ensure_layout,
)
from batchward.bridge.marg_layout import TABLES, create_schema


@pytest.fixture
def marg(connection):
    create_schema(connection)
    return connection


def test_a_database_created_from_the_layout_matches_it(marg):
    assert check_layout(marg) == []
    ensure_layout(marg)


def test_an_exported_business_matches_the_layout(exported):
    assert check_layout(exported) == []


def test_reports_a_missing_table(marg):
    marg.execute('DROP TABLE "PROBAT"')
    assert check_layout(marg) == [LayoutMismatch("PROBAT", None, "table is missing")]


def test_reports_a_dropped_column(marg):
    marg.execute('ALTER TABLE "ORDER" DROP COLUMN GSTIN')
    assert check_layout(marg) == [LayoutMismatch("ORDER", "GSTIN", "column is missing")]


def test_a_renamed_column_is_reported_as_missing(marg):
    marg.execute('ALTER TABLE "ORDER" RENAME COLUMN DLNO TO DL_NO')
    assert check_layout(marg) == [LayoutMismatch("ORDER", "DLNO", "column is missing")]


def test_reports_a_column_whose_type_changed(connection):
    columns = dict(TABLES["DIS"], QTY="TEXT")
    column_sql = ", ".join(f'"{name}" {kind}' for name, kind in columns.items())
    create_schema(connection)
    connection.execute('DROP TABLE "DIS"')
    connection.execute(f'CREATE TABLE "DIS" ({column_sql})')
    assert check_layout(connection) == [
        LayoutMismatch("DIS", "QTY", "expected INTEGER, found TEXT")
    ]


def test_tolerates_extra_columns_added_by_an_upgrade(marg):
    marg.execute('ALTER TABLE "PRO" ADD COLUMN BARCODE TEXT')
    assert check_layout(marg) == []


def test_matches_column_names_regardless_of_case(connection):
    column_sql = ", ".join(f'"{name.lower()}" {kind}' for name, kind in TABLES["ORDER"].items())
    connection.execute(f'CREATE TABLE "ORDER" ({column_sql})')
    assert check_layout(connection, {"ORDER": TABLES["ORDER"]}) == []


def test_ensure_layout_stops_the_sync_and_lists_every_problem(marg):
    marg.execute('DROP TABLE "PROBAT"')
    marg.execute('ALTER TABLE "ORDER" DROP COLUMN GSTIN')
    with pytest.raises(MargLayoutError) as caught:
        ensure_layout(marg)
    message = str(caught.value)
    assert "sync stopped" in message
    assert "ORDER.GSTIN: column is missing" in message
    assert "PROBAT: table is missing" in message
    assert len(caught.value.mismatches) == 2
