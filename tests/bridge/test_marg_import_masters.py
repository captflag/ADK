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
