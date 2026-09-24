import csv
import io
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from batchward.buying.order import (
    ORDER_COLUMNS,
    OpenOrder,
    draft_order,
    order_posting,
    still_due,
)
from batchward.buying.planning import place
from batchward.buying.suggest import Policy, last_rates, suggest
from batchward.core.approvals import Approval
from batchward.core.ledger import Ledger
from batchward.core.models import Item, Location, MovementType, Party, PartyKind
from batchward.core.orders import OrderLine, PurchaseOrder
from batchward.records.store import MIGRATIONS, RecordsError, RecordStore
from factories import at, batch_key, movement

ON = date(2026, 3, 1)
GODOWN = Location("GODOWN", "Godown")
RETURNS = Location("RETURNS", "Returns", sellable=False)
COMPANY = Party("C01", PartyKind.COMPANY, "Aravalli Pharma")


def item(item_id="I001", company_id="C01"):
    return Item(
        id=item_id,
        company_id=company_id,
        brand=f"Brand {item_id}",
        molecule="Paracetamol",
        strength="500 mg",
        unit="strip of 10 tablets",
        hsn="3004",
        gst_rate=Decimal("0.05"),
        mrp=Decimal("30.00"),
    )


ITEMS = {"I001": item(), "I002": item("I002"), "I003": item("I003", "C02")}
ONE_COVER = Policy(cover_days=21)
"""One cover of 21 days for every item: 10.5 days of safety and 10.5 per order (ADR 0020)."""


def bought(*entries, day=1):
    """A ledger of (key, units, location, rate) bought in January."""
    return Ledger(
        replace(
            movement(MovementType.PURCHASE, units, when=at(day), batch=key, location_id=place_),
            rate=Decimal(rate),
        )
        for key, units, place_, rate in entries
    )


def suggestions(ledger, rates, **kwargs):
    return {s.item.id: s for s in suggest(ledger, ITEMS, [GODOWN, RETURNS], rates, on=ON, **kwargs)}


def test_an_item_below_its_reorder_point_is_ordered_up_to_its_cover():
    key = batch_key(item_id="I001", expiry=date(2027, 12, 31))
    got = suggestions(bought((key, 30, "GODOWN", "12.50")), {"I001": 4.0}, policy=ONE_COVER)
    got = got["I001"]
    # reorder point 4 x (4 + 10.5) = 58, order up to 4 x 25 = 100, position 30.
    assert (got.reorder_point, got.order_up_to, got.usable, got.quantity) == (58, 100, 30, 70)
    assert got.value == Decimal("875.00")
    assert got.days_of_cover == 7.5


def test_enough_stock_or_enough_coming_means_nothing_to_order():
    key = batch_key(item_id="I001", expiry=date(2027, 12, 31))
    ledger = bought((key, 30, "GODOWN", "12.50"))
    assert suggestions(ledger, {"I001": 1.0})["I001"].quantity == 0
    coming = suggestions(ledger, {"I001": 4.0}, due={"I001": 40})["I001"]
    assert (coming.due, coming.quantity) == (40, 0)


def test_stock_that_will_expire_unsold_held_or_off_the_shelf_does_not_count():
    soon = batch_key("SOON", item_id="I001", expiry=date(2026, 3, 11))
    held = batch_key("HELD", item_id="I001", expiry=date(2027, 12, 31))
    later = batch_key("LATER", item_id="I001", expiry=date(2027, 12, 31))
    ledger = bought(
        (soon, 50, "GODOWN", "10"),
        (held, 40, "GODOWN", "10"),
        (later, 20, "RETURNS", "10"),
    )
    got = suggestions(ledger, {"I001": 2.0}, held=[held], policy=ONE_COVER)["I001"]
    # 10 days sell 20 of the 50 expiring soon; the held batch and the returns shelf are out.
    assert got.usable == 20
    assert got.quantity == 30  # below the reorder point of 29, so up to 50


def test_items_that_do_not_sell_are_never_ordered_and_policy_is_checked():
    assert suggestions(Ledger(), {"I001": 0.0}) == {}
    assert suggestions(Ledger(), {"UNKNOWN": 3.0}) == {}
    with pytest.raises(ValueError, match="at least a day"):
        Policy(cover_days=0)


def test_the_last_purchase_rate_values_the_order():
    first = replace(
        movement(MovementType.PURCHASE, 5, when=at(9), batch=batch_key("B", item_id="I001")),
        rate=Decimal("11.00"),
    )
    ledger = bought((batch_key("A", item_id="I001"), 5, "GODOWN", "10.00"))
    ledger.append(first)
    assert last_rates(ledger) == {"I001": Decimal("11.00")}


def test_an_order_takes_one_companys_items_and_its_files_say_what_to_supply():
    key = batch_key(item_id="I001", expiry=date(2027, 12, 31))
    other = batch_key("X", item_id="I003", company_id="C02", expiry=date(2027, 12, 31))
    got = suggest(
        bought((key, 10, "GODOWN", "12.50"), (other, 10, "GODOWN", "9.00")),
        ITEMS,
        [GODOWN],
        {"I001": 4.0, "I002": 1.0, "I003": 4.0},
        on=ON,
        policy=ONE_COVER,
    )
    order = draft_order(got, company_id="C01", on=ON)
    assert order == PurchaseOrder(
        "PO/C01/260301", "C01", ON, (OrderLine("I001", 90), OrderLine("I002", 25))
    )
    assert draft_order(got, company_id="C09", on=ON) is None
    posting = order_posting(order, COMPANY, {s.item.id: s for s in got})
    assert posting.approval_id == "order:PO/C01/260301"
    assert posting.summary.startswith("PO/C01/260301 on Aravalli Pharma: 115 units of 2 products")
    sheet = list(csv.reader(io.StringIO(posting.files["PO-C01-260301.order.csv"])))
    assert tuple(sheet[0]) == ORDER_COLUMNS
    assert [(row[2], row[5], row[7]) for row in sheet[1:]] == [
        ("I001", "90", "1125.00"),
        ("I002", "25", ""),
    ]
    message = posting.files["PO-C01-260301.order.txt"]
    assert "Brand I001 (strip of 10 tablets): 90" in message
    assert "Please quote PO/C01/260301 on your invoice." in message


def test_units_due_count_on_recent_orders_and_old_ones_are_overdue():
    recent = PurchaseOrder("PO/C01/260220", "C01", date(2026, 2, 20), (OrderLine("I001", 50),))
    old = PurchaseOrder("PO/C01/260115", "C01", date(2026, 1, 15), (OrderLine("I002", 30),))
    done = PurchaseOrder("PO/C01/260210", "C01", date(2026, 2, 10), (OrderLine("I001", 10),))
    due, overdue = still_due(
        [
            OpenOrder(recent, {"I001": 20}),
            OpenOrder(old, {}),
            OpenOrder(done, {"I001": 10}),
        ],
        on=ON,
    )
    assert due == {"I001": 30}
    assert [o.order.number for o in overdue] == ["PO/C01/260115"]
    assert OpenOrder(old).age(ON) == 45


def test_orders_read_back_from_the_records_and_an_approved_order_is_placed_once(tmp_path):
    order = PurchaseOrder("PO/C01/260301", "C01", ON, (OrderLine("I001", 90),))
    key = batch_key(item_id="I001", expiry=date(2027, 12, 31))
    got = suggest(bought((key, 10, "GODOWN", "12.50")), ITEMS, [GODOWN], {"I001": 4.0}, on=ON)
    posting = order_posting(order, COMPANY, {s.item.id: s for s in got})
    other = order_posting(
        PurchaseOrder("PO/C01/260301", "C01", ON, (OrderLine("I001", 91),)),
        COMPANY,
        {s.item.id: s for s in got},
    )
    when = datetime(2026, 3, 1, 10, tzinfo=UTC)
    with RecordStore(tmp_path / "records.sqlite") as store:
        with pytest.raises(RecordsError, match="has changed since it was put up"):
            place(store, order, other, posting, approved_by="Ravi", at=when, out=tmp_path)
        first = place(store, order, posting, posting, approved_by="Ravi", at=when, out=tmp_path)
        assert len(first.written) == 2
        again = place(store, order, posting, posting, approved_by="Asha", at=when, out=tmp_path)
        assert again.written == () and again.approval.approved_by == "Ravi"
        assert store.orders() == [order]
        assert store.order("po/c01/ 260301") == order
        with pytest.raises(RecordsError, match="cannot record order"):
            store.save_order(order, "order:PO/C01/260301")
        with pytest.raises(RecordsError, match="cannot record order"):
            store.save_order(
                PurchaseOrder("PO/C01/260302", "C01", ON, (OrderLine("I001", 1),)), "unknown"
            )


def test_a_database_from_the_seventh_schema_gains_the_order_tables(tmp_path):
    path = tmp_path / "records.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.executescript(
            f"BEGIN; {''.join(MIGRATIONS[:7])} PRAGMA user_version = 7; COMMIT;"
        )
    order = PurchaseOrder("PO/C01/260301", "C01", ON, (OrderLine("I001", 9),))
    with RecordStore(path) as store:
        assert store.orders() == []
        store.save_approval(
            Approval("order:PO/C01/260301", "purchase order", "Ravi",
                     datetime(2026, 3, 1, tzinfo=UTC), "0" * 64, "an order")
        )  # fmt: skip
        store.save_order(order, "order:PO/C01/260301")
    with (
        closing(sqlite3.connect(path)) as connection,
        pytest.raises(sqlite3.IntegrityError, match="only ever added to"),
    ):
        connection.execute("DELETE FROM purchase_orders")
