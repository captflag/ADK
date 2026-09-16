from datetime import date, time
from decimal import Decimal

import pytest

from batchward.bridge.marg_layout import (
    TABLES,
    VOUCHER_TYPES,
    create_schema,
    format_date,
    format_expiry,
    format_gst,
    format_time,
    parse_date,
    parse_expiry,
    parse_gst,
    parse_money,
    parse_time,
)
from batchward.core.models import INBOUND, OUTBOUND


def test_creates_every_table_with_its_expected_columns(connection):
    create_schema(connection)
    for table, columns in TABLES.items():
        found = {row[1]: row[2] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        assert found == columns


def test_the_order_table_works_despite_its_reserved_name(connection):
    create_schema(connection)
    connection.execute(
        'INSERT INTO "ORDER" (CODE, NAME, TYPE) VALUES (?, ?, ?)', ("C01", "X", "CO")
    )
    assert connection.execute('SELECT NAME FROM "ORDER"').fetchone() == ("X",)


def test_dates_round_trip_in_indian_format():
    assert format_date(date(2026, 2, 12)) == "12/02/2026"
    assert parse_date("12/02/2026") == date(2026, 2, 12)


def test_times_round_trip_to_the_minute():
    assert parse_time(format_time(time(9, 5))) == time(9, 5)


def test_expiry_is_stored_as_month_and_year():
    assert format_expiry(date(2027, 10, 31)) == "10/2027"


def test_parsed_expiry_is_the_last_day_of_the_month():
    assert parse_expiry("10/2027") == date(2027, 10, 31)
    assert parse_expiry("02/2028") == date(2028, 2, 29)


def test_refuses_an_expiry_that_is_not_a_month_end():
    with pytest.raises(ValueError, match="not a month end"):
        format_expiry(date(2027, 10, 15))


@pytest.mark.parametrize(("fraction", "percent"), [("0.05", 5.0), ("0.12", 12.0), ("0", 0.0)])
def test_gst_converts_between_fraction_and_percentage(fraction, percent):
    assert format_gst(Decimal(fraction)) == percent
    assert parse_gst(percent) == Decimal(fraction)


def test_money_is_read_back_to_the_paisa_without_float_noise():
    assert parse_money(0.1 + 0.2) == Decimal("0.30")


def test_every_voucher_type_moves_stock_in_its_kinds_direction():
    for code, (kind, sign) in VOUCHER_TYPES.items():
        if kind in INBOUND:
            assert sign == +1, code
        if kind in OUTBOUND:
            assert sign == -1, code
