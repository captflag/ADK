import sqlite3
from contextlib import closing
from datetime import date

import pytest

from batchward.buying.cases import (
    CaseSize,
    CaseSizesFileError,
    CaseTable,
    Rounding,
    read_case_sizes,
    to_cases,
)
from batchward.records.store import MIGRATIONS, RecordsError, RecordStore

ON = date(2026, 3, 1)


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
