import csv
import io
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from batchward.buying.cases import (
    CaseSize,
    CaseSizesFileError,
    CaseTable,
    Rounding,
    read_case_sizes,
    to_cases,
)
from batchward.buying.cover import Cover
from batchward.buying.order import ORDER_COLUMNS, draft_order, order_posting
from batchward.buying.replay import replay
from batchward.buying.suggest import Policy, suggest
from batchward.core.ledger import Ledger
from batchward.core.models import Item, Location, MovementType, Party, PartyKind
from batchward.records.store import MIGRATIONS, RecordsError, RecordStore
from factories import at, batch_key, movement

ON = date(2026, 3, 1)
GODOWN = Location("GODOWN", "Godown")
COMPANY = Party("C01", PartyKind.COMPANY, "Aravalli Pharma")


def item(item_id):
    return Item(
        id=item_id,
        company_id="C01",
        brand=f"Brand {item_id}",
        molecule="Paracetamol",
        strength="500 mg",
        unit="strip of 10 tablets",
        hsn="3004",
        gst_rate=Decimal("0.05"),
        mrp=Decimal("30.00"),
    )


def size(item_id="I001", units=25, day=date(2026, 1, 1), reference="Price list 2026"):
    return CaseSize(item_id, units, day, reference)


@pytest.mark.parametrize(
    ("units", "case", "rounding", "expected"),
    [
        (70, 25, Rounding.UP, 75),
        (51, 25, Rounding.UP, 75),
        (50, 25, Rounding.UP, 50),
        (62, 25, Rounding.NEAREST, 50),
        (63, 25, Rounding.NEAREST, 75),
        (5, 25, Rounding.NEAREST, 25),  # never less than one case
        (0, 25, Rounding.UP, 0),
        (-3, 25, Rounding.UP, 0),
        (7, 1, Rounding.UP, 7),
    ],
)
def test_an_order_is_rounded_to_whole_cases(units, case, rounding, expected):
    assert to_cases(units, case, rounding) == expected


def test_a_case_holds_at_least_one_unit_and_names_where_its_size_comes_from():
    with pytest.raises(ValueError, match="at least one unit"):
        to_cases(10, 0)
    with pytest.raises(ValueError, match="must hold at least one unit"):
        size(units=0)
    with pytest.raises(ValueError, match="needs a reference"):
        size(reference=" ")
    with pytest.raises(ValueError, match="needs the product's code"):
        size(item_id="")


def test_a_case_size_is_in_force_from_its_date_until_a_later_one():
    table = CaseTable([size(units=50, day=date(2026, 4, 1)), size(), size("I002", 10)])
    assert table.in_force("I001", date(2025, 12, 31)) is None
    assert table.in_force("I001", date(2026, 3, 31)).units == 25
    assert table.in_force("I001", date(2026, 4, 1)).units == 50
    assert table.in_force("I999", ON) is None
    assert len(table) == 3
    assert [s.units for s in table] == [25, 50, 10]


def test_case_sizes_are_read_from_csv_naming_any_line_that_cannot_be():
    lines = [
        "Product code,Units per case,Effective from,Reference",
        "I001,25,01/01/2026,Price list 2026",
        ",,,",
        "I002,10,15/01/2026,Delivery note ARA/1",
    ]
    assert read_case_sizes(lines) == [
        size(),
        size("I002", 10, date(2026, 1, 15), "Delivery note ARA/1"),
    ]
    with pytest.raises(CaseSizesFileError, match="no column Reference"):
        read_case_sizes(["Product code,Units per case,Effective from", "I001,25,01/01/2026"])
    with pytest.raises(CaseSizesFileError, match=r"line 3: .*one"):
        read_case_sizes([lines[0], lines[1], "I002,one,01/01/2026,x"])
    with pytest.raises(CaseSizesFileError, match="line 2: "):
        read_case_sizes([lines[0], "I001,25,2026-01-01,x"])


def bought(units=10):
    key = batch_key(item_id="I001", expiry=date(2027, 12, 31))
    purchase = movement(MovementType.PURCHASE, units, when=at(1), batch=key)
    return Ledger([replace(purchase, rate=Decimal("12.50"))])


def test_an_order_is_rounded_up_to_whole_cases_and_says_how_long_a_case_lasts():
    one_cover = Policy(cover_days=21)
    rates, items = {"I001": 4.0}, {"I001": item("I001")}
    (units,) = suggest(bought(), items, [GODOWN], rates, on=ON, policy=one_cover)
    (cased,) = suggest(bought(), items, [GODOWN], rates, on=ON, policy=one_cover,
                       cases=CaseTable([size()]))  # fmt: skip
    # Up to 4 x 25 = 100 from 10 on hand: 90 needed, four cases of 25.
    assert (units.quantity, units.case_units, units.cases) == (90, None, None)
    assert (cased.needed, cased.quantity, cased.case_units, cased.cases) == (90, 100, 25, 4)
    assert cased.value == Decimal("1250.00")
    assert cased.case_days == 6.25 and not cased.slow_case
    (slow,) = suggest(bought(), items, [GODOWN], {"I001": 0.2}, on=ON, policy=one_cover,
                      cases=CaseTable([size()]))  # fmt: skip
    assert slow.quantity == 0 and slow.case_days == 125 and not slow.slow_case
    (slow,) = suggest(Ledger(), items, [GODOWN], {"I001": 0.2}, on=ON, policy=one_cover,
                      cases=CaseTable([size()]))  # fmt: skip
    assert slow.quantity == 25 and slow.slow_case


def test_the_order_sheet_and_message_say_how_many_cases():
    items = {"I001": item("I001"), "I002": item("I002")}
    got = suggest(
        bought(),
        items,
        [GODOWN],
        {"I001": 4.0, "I002": 1.0},
        on=ON,
        policy=Policy(cover_days=21),
        cases=CaseTable([size(), size("I002", 25)]),
    )
    order = draft_order(got, company_id="C01", on=ON)
    posting = order_posting(order, COMPANY, {s.item.id: s for s in got})
    sheet = list(csv.reader(io.StringIO(posting.files["PO-C01-260301.order.csv"])))
    assert tuple(sheet[0]) == ORDER_COLUMNS
    assert [(row[2], row[5], row[8], row[9]) for row in sheet[1:]] == [
        ("I001", "100", "4", "25"),
        ("I002", "25", "1", "25"),
    ]
    message = posting.files["PO-C01-260301.order.txt"]
    assert "Brand I001 (strip of 10 tablets): 100, 4 cases of 25" in message
    assert "Brand I002 (strip of 10 tablets): 25, 1 case of 25" in message


def test_a_replay_orders_in_whole_cases_by_its_rounding():
    demand, rates = [10] * 120, [10.0] * 120

    def run(**cases):
        return replay(demand, rates, Cover(7, 7), start=0, end=120, took=[4] * 120, cost=1.0,
                      **cases)  # fmt: skip

    # Each order needs 80 units: in cases of 60, up is two cases and the nearest is one.
    units, up, near = run(), run(case=60), run(case=60, rounding=Rounding.NEAREST)
    assert up.fill == units.fill == near.fill == 1.0
    assert up.lines < units.lines < near.lines
    assert near.stock_value < units.stock_value < up.stock_value


def test_case_sizes_are_recorded_once_and_a_different_one_for_the_same_date_refused(tmp_path):
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        assert store.save_case_size(size())
        assert not store.save_case_size(size())
        with pytest.raises(RecordsError, match="a different case size for I001 from 2026-01-01"):
            store.save_case_size(size(units=50))
        assert store.save_case_size(size(units=50, day=date(2026, 4, 1)))
        assert store.case_table().in_force("I001", ON).units == 25
    with (
        closing(sqlite3.connect(path)) as connection,
        pytest.raises(sqlite3.IntegrityError, match="only ever added to"),
    ):
        connection.execute("UPDATE case_sizes SET units = 10")


def test_a_database_from_the_ninth_schema_gains_the_case_sizes_table(tmp_path):
    path = tmp_path / "records.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.executescript(
            f"BEGIN; {''.join(MIGRATIONS[:9])} PRAGMA user_version = 9; COMMIT;"
        )
    with RecordStore(path) as store:
        assert store.case_sizes() == []
        store.save_case_size(size())
        assert store.case_sizes() == [size()]
