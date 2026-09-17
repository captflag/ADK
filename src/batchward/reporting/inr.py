"""Rupee amounts in Indian digit grouping.

Indian numbering groups the last three digits, then pairs: 12,34,567 is
twelve lakh thirty-four thousand five hundred sixty-seven. Formatting only
changes how a figure looks; rounding follows ``ROUND_HALF_UP``, the way a
person rounds on paper, and happens after the figure is final.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def group_indian(digits: str) -> str:
    """Group a string of digits the Indian way: '1234567' becomes '12,34,567'."""
    if not digits.isdigit():
        raise ValueError(f"expected digits only, got {digits!r}")
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *pairs, tail])


def format_inr(amount: Decimal, *, paise: bool = False) -> str:
    """'₹12,34,567', or '₹12,34,567.50' with paise; negatives as '-₹4,500'."""
    places = Decimal("0.01") if paise else Decimal(1)
    rounded = amount.quantize(places, rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    whole, _, fraction = f"{abs(rounded):f}".partition(".")
    text = group_indian(whole)
    if paise:
        text = f"{text}.{fraction}"
    return f"{sign}₹{text}"
