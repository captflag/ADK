from decimal import Decimal

import pytest

from batchward.reporting.inr import format_inr, group_indian


@pytest.mark.parametrize(
    ("digits", "grouped"),
    [
        ("0", "0"),
        ("999", "999"),
        ("1000", "1,000"),
        ("99999", "99,999"),
        ("100000", "1,00,000"),
        ("1234567", "12,34,567"),
        ("123456789", "12,34,56,789"),
    ],
)
def test_groups_digits_the_indian_way(digits, grouped):
    assert group_indian(digits) == grouped


def test_rejects_anything_but_digits():
    with pytest.raises(ValueError, match="digits only"):
        group_indian("12.5")


def test_formats_whole_rupees_rounding_half_up():
    assert format_inr(Decimal("1234567.49")) == "₹12,34,567"
    assert format_inr(Decimal("1234567.50")) == "₹12,34,568"


def test_can_show_paise():
    assert format_inr(Decimal("184210.5"), paise=True) == "₹1,84,210.50"


def test_negative_amounts_put_the_sign_before_the_rupee_symbol():
    assert format_inr(Decimal("-4500")) == "-₹4,500"


def test_zero_and_a_tiny_negative_that_rounds_to_zero_carry_no_sign():
    assert format_inr(Decimal(0)) == "₹0"
    assert format_inr(Decimal("-0.4")) == "₹0"
