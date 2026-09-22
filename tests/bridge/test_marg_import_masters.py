from collections import defaultdict

import pytest

from batchward.bridge.marg_import import MargDataError, read_masters


def test_parties_come_back_unchanged(exported, business):
    masters = read_masters(exported)
    original = {p.id: p for p in [*business.catalogue.companies, *business.chemists]}
    assert masters.parties == original


def test_items_come_back_unchanged_including_gst_mrp_and_schedules(exported, business):
    masters = read_masters(exported)
    assert masters.items == {item.id: item for item in business.catalogue.items}


def test_batches_come_back_unchanged(exported, business):
    assert read_masters(exported).batches == business.batches


def test_marg_stock_per_batch_matches_the_ledger(exported, business):
    expected = defaultdict(int)
    for (key, _location), qty in business.ledger.balances().items():
        expected[key] += qty
    stock = read_masters(exported).stock
    assert {key: qty for key, qty in stock.items() if qty} == dict(expected)


def test_rejects_a_party_of_unknown_type(exported):
    exported.execute("UPDATE \"ORDER\" SET TYPE = 'ZZ' WHERE CODE = 'C01'")
    with pytest.raises(MargDataError, match="party C01 has unknown type 'ZZ'"):
        read_masters(exported)


def test_rejects_an_item_from_an_unknown_company(exported):
    code = exported.execute('SELECT CODE FROM "PRO" LIMIT 1').fetchone()[0]
    exported.execute("UPDATE \"PRO\" SET COMPANY = 'C99' WHERE CODE = ?", (code,))
    with pytest.raises(MargDataError, match="unknown company 'C99'"):
        read_masters(exported)


def test_rejects_a_batch_for_an_unknown_item(exported):
    exported.execute("UPDATE \"PROBAT\" SET PCODE = 'NOPE' WHERE BATCH = 'AZ4021'")
    with pytest.raises(MargDataError, match="batch AZ4021 refers to unknown item 'NOPE'"):
        read_masters(exported)


def test_a_batch_number_typed_in_another_case_is_the_same_batch_with_its_stock_added(exported):
    row = exported.execute("SELECT * FROM \"PROBAT\" WHERE BATCH = 'AZ4021'").fetchone()
    exported.execute(
        'INSERT INTO "PROBAT" VALUES (?, ?, ?, ?, ?, ?)', (*row[:1], "az4021", *row[2:5], 5)
    )
    masters = read_masters(exported)
    (key,) = [key for key in masters.batches if key.batch_no == "AZ4021"]
    assert masters.stock[key] == row[5] + 5


def test_rows_for_one_batch_that_disagree_are_refused_naming_both(exported):
    row = exported.execute("SELECT * FROM \"PROBAT\" WHERE BATCH = 'AZ4021'").fetchone()
    exported.execute(
        'INSERT INTO "PROBAT" VALUES (?, ?, ?, ?, ?, ?)', (row[0], "az4021", *row[2:4], 99.0, 5)
    )
    with pytest.raises(MargDataError, match="batch rows AZ4021 and az4021 of item C01-001"):
        read_masters(exported)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("UPDATE \"ORDER\" SET DLNO = NULL WHERE CODE = 'CH001'", "party CH001: a chemist must"),
        ("UPDATE \"PRO\" SET MRP = 'free' WHERE CODE = 'C01-001'", "item C01-001: "),
        ("UPDATE \"PRO\" SET GST = NULL WHERE CODE = 'C01-001'", "item C01-001: "),
        ("UPDATE \"PROBAT\" SET EXPIRY = '13/2027' WHERE BATCH = 'AZ4021'", "batch AZ4021 of item"),
        ("UPDATE \"PROBAT\" SET MFG = NULL WHERE BATCH = 'AZ4021'", "batch AZ4021 of item"),
    ],
)
def test_a_row_that_cannot_be_read_is_refused_naming_it(exported, change, message):
    exported.execute(change)
    with pytest.raises(MargDataError, match=message):
        read_masters(exported)


def test_an_item_with_blank_optional_fields_is_read_with_blanks(exported):
    exported.execute(
        'UPDATE "PRO" SET SCHEDULE = NULL, SALT = NULL, STRENGTH = NULL, PACK = NULL, HSN = NULL '
        "WHERE CODE = 'C01-001'"
    )
    item = read_masters(exported).items["C01-001"]
    assert (item.schedules, item.molecule, item.strength, item.unit, item.hsn) == (
        frozenset(),
        "",
        "",
        "",
        "",
    )


def test_an_item_without_a_brand_name_is_refused(exported):
    exported.execute("UPDATE \"PRO\" SET NAME = '  ' WHERE CODE = 'C01-001'")
    with pytest.raises(MargDataError, match="item C01-001 has no brand name"):
        read_masters(exported)
