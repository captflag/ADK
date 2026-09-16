from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest

from batchward.bridge.marg_export import export_to_marg
from batchward.bridge.marg_layout import VOUCHER_TYPES, format_expiry
from batchward.core.ledger import Ledger
from batchward.core.models import Batch, MovementType
from factories import at, batch_key, movement


def count(connection, table):
    return connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


@pytest.fixture
def exported(connection, business_records):
    export_to_marg(connection, **business_records)
    return connection


def test_writes_one_row_per_party_item_batch_and_movement(exported, business):
    assert count(exported, "ORDER") == len(business.catalogue.companies) + len(business.chemists)
    assert count(exported, "PRO") == len(business.catalogue.items)
    assert count(exported, "PROBAT") == len(business.batches)
    assert count(exported, "DIS") == len(business.ledger)


def test_quantities_are_positive_with_a_known_voucher_type(exported):
    rows = exported.execute('SELECT VTYPE, QTY FROM "DIS"').fetchall()
    assert all(vtype in VOUCHER_TYPES and qty > 0 for vtype, qty in rows)


def test_batch_stock_matches_the_ledger_across_locations(exported, business):
    expected = defaultdict(int)
    for (key, _location), qty in business.ledger.balances().items():
        expected[(key.item_id, key.batch_no, format_expiry(key.expiry))] += qty
    rows = exported.execute('SELECT PCODE, BATCH, EXPIRY, STOCK FROM "PROBAT"')
    for pcode, batch_no, expiry, stock in rows:
        assert stock == expected.get((pcode, batch_no, expiry), 0)


def test_the_recalled_batch_is_written_with_a_printed_expiry(exported):
    row = exported.execute("SELECT EXPIRY, STOCK FROM \"PROBAT\" WHERE BATCH = 'AZ4021'").fetchone()
    assert row == ("10/2027", 210)


def test_sales_carry_the_chemist_and_a_local_time(exported):
    party, vdate, vtime = exported.execute(
        "SELECT PARTY, VDATE, VTIME FROM \"DIS\" WHERE VTYPE = 'S' LIMIT 1"
    ).fetchone()
    assert party.startswith("CH")
    assert len(vdate) == 10 and vdate[2] == "/"
    assert len(vtime) == 5 and vtime[2] == ":"


def test_lines_of_one_voucher_are_numbered_from_one(exported):
    lines = [row[0] for row in exported.execute("SELECT LINE FROM \"DIS\" WHERE VNO = 'OPENING'")]
    assert sorted(lines) == list(range(1, len(lines) + 1))


def _tiny(**overrides):
    key = batch_key(expiry=date(2027, 10, 31))
    batches = {key: Batch(key=key, manufactured=date(2025, 11, 1), mrp=Decimal(70))}
    fields = {"parties": [], "items": [], "batches": batches, "ledger": Ledger()}
    return fields | overrides


def test_refuses_to_export_a_reversal(connection):
    purchase = movement(MovementType.PURCHASE, 10, when=at(1))
    ledger = Ledger([purchase])
    ledger.reverse(purchase.id, reversal_id="R1", at=at(2), document_ref="CORR-1")
    with pytest.raises(ValueError, match="edited bills"):
        export_to_marg(connection, **_tiny(ledger=ledger))


def test_refuses_a_movement_whose_batch_has_no_record(connection):
    ledger = Ledger([movement(MovementType.PURCHASE, 10, when=at(1), batch=batch_key("GHOST"))])
    with pytest.raises(ValueError, match="no batch record"):
        export_to_marg(connection, **_tiny(ledger=ledger))


def test_a_refused_export_leaves_the_database_empty(connection):
    purchase = movement(MovementType.PURCHASE, 10, when=at(1))
    ledger = Ledger([purchase])
    ledger.reverse(purchase.id, reversal_id="R1", at=at(2), document_ref="CORR-1")
    with pytest.raises(ValueError):
        export_to_marg(connection, **_tiny(ledger=ledger))
    assert connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
