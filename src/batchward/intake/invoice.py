"""A supplier's tax invoice as printed, and reading its figures.

This is the schema an extraction model fills from a photo or PDF, so every
field holds text exactly as printed: "1,234.50", "10/27", "12-Feb-2026". Nothing
is converted while extracting. Checks in plain Python then read each field, and
a field that does not read is sent back to extraction as a precise hint, rather
than a model guessing a number into shape (ADR 0003, ADR 0015).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field


class InvoiceLine(BaseModel):
    description: str = Field(description="The product as printed, e.g. 'AZINIL 500 TAB 3'S'")
    hsn: str = Field("", description="HSN code as printed")
    batch_no: str = Field(description="Batch number exactly as printed; never corrected")
    expiry: str = Field(description="Expiry as printed, e.g. '10/27' or 'OCT-2027'")
    quantity: str = Field(description="Units billed, as printed")
    free_quantity: str = Field("", description="Free units, as printed; empty if none")
    mrp: str = Field(description="Maximum retail price per unit, as printed")
    rate: str = Field(description="Price to the stockist per unit before GST, as printed")
    discount_percent: str = Field("", description="Discount percent, as printed; empty if none")
    gst_percent: str = Field(description="GST rate in percent, as printed")
    taxable_value: str = Field(description="The line's value before GST, as printed")
    tax_amount: str = Field(description="The line's GST, CGST and SGST or IGST added, as printed")


class PurchaseInvoice(BaseModel):
    supplier_name: str
    supplier_address: str = ""
    supplier_gstin: str = ""
    supplier_licence: str = Field("", description="Supplier's drug licence number(s)")
    invoice_no: str
    invoice_date: str = Field(description="As printed, e.g. '12/02/2026'")
    order_no: str = Field("", description="The buyer's purchase order number, as printed")
    buyer_gstin: str = ""
    lines: list[InvoiceLine]
    taxable_total: str
    tax_total: str
    round_off: str = ""
    grand_total: str


def amount(text: str) -> Decimal | None:
    """A printed amount: "₹1,234.50", "Rs. 64.20", "(0.40)" for a negative. None if unreadable."""
    cleaned = re.sub(r"(?i)₹|rs\.?|inr|\s|,", "", text)
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cleaned):
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def count(text: str) -> int | None:
    """A printed whole number of units, or None if it is not one."""
    cleaned = re.sub(r"\s|,", "", text)
    return int(cleaned) if re.fullmatch(r"\d+", cleaned) else None
