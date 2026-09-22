from datetime import date
from decimal import Decimal

import pytest

from batchward.core.printed import printed_date
from batchward.intake.invoice import amount, count


@pytest.mark.parametrize(
    ("printed", "value"),
    [
        ("64.20", Decimal("64.20")),
        ("₹1,234.50", Decimal("1234.50")),
        ("Rs. 2,642.00", Decimal("2642.00")),
        ("-0.40", Decimal("-0.40")),
        ("(0.40)", Decimal("-0.40")),
        ("64.2O", None),
        ("", None),
    ],
)
def test_reads_a_printed_amount(printed, value):
    assert amount(printed) == value


@pytest.mark.parametrize(("printed", "value"), [("50", 50), ("1,200", 1200), ("5O", None)])
def test_reads_a_printed_count(printed, value):
    assert count(printed) == value


@pytest.mark.parametrize(
    ("printed", "day"),
    [
        ("12/02/2026", date(2026, 2, 12)),
        ("12-02-26", date(2026, 2, 12)),
        ("12.02.2026", date(2026, 2, 12)),
        ("12-Feb-2026", date(2026, 2, 12)),
        ("12 February 2026", date(2026, 2, 12)),
        ("2026-02-12", date(2026, 2, 12)),
        ("31/02/2026", None),
        ("12/2026", None),
    ],
)
def test_reads_a_printed_date_day_first(printed, day):
    assert printed_date(printed) == day
