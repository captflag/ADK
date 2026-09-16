from batchward.bridge.marg_import import read_ledger, read_masters
from batchward.bridge.reconcile import reconcile_stock


def reconcile(connection):
    masters = read_masters(connection)
    return reconcile_stock(masters, read_ledger(connection, masters))


def test_a_clean_export_reconciles_with_no_differences(marg_template, rebuilt):
    assert reconcile_stock(read_masters(marg_template), rebuilt) == []


def test_a_hand_edited_stock_figure_shows_up(exported, recall):
    exported.execute("UPDATE \"PROBAT\" SET STOCK = 250 WHERE BATCH = 'AZ4021'")
    (difference,) = reconcile(exported)
    assert difference.batch == recall.batch
    assert (difference.marg_stock, difference.ledger_stock, difference.difference) == (250, 210, 40)


def test_a_bill_deleted_after_the_fact_shows_up(exported, recall):
    vno, line, qty = exported.execute(
        "SELECT VNO, LINE, QTY FROM \"DIS\" WHERE BATCH = 'AZ4021' AND VTYPE = 'S' LIMIT 1"
    ).fetchone()
    exported.execute('DELETE FROM "DIS" WHERE VNO = ? AND LINE = ?', (vno, line))
    (difference,) = reconcile(exported)
    assert difference.batch == recall.batch
    assert difference.ledger_stock == 210 + qty
    assert difference.difference == -qty


def test_largest_gaps_are_listed_first(exported):
    rows = [row[0] for row in exported.execute('SELECT rowid FROM "PROBAT" LIMIT 3')]
    for offset, rowid in zip((1, 30, 5), rows, strict=True):
        exported.execute('UPDATE "PROBAT" SET STOCK = STOCK + ? WHERE rowid = ?', (offset, rowid))
    assert [d.difference for d in reconcile(exported)] == [30, 5, 1]
