"""The clerk reading a simulated invoice with the real Gemini API.

Marked live and skipped without GOOGLE_API_KEY. It checks what must not vary:
every batch number, expiry, quantity and amount is copied exactly as printed,
and the reading checks as ready to post.

Run with: uv run --env-file .env pytest -m live
"""

import asyncio
import os
from datetime import date

import pytest
from google.genai import types

from batchward.core.printed import printed_date
from batchward.intake.checks import check_invoice
from batchward.intake.extract import extract_invoice
from batchward.sim.business import SimConfig, simulate
from batchward.sim.invoices import purchase_invoices, render_text

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("GOOGLE_API_KEY"), reason="needs GOOGLE_API_KEY"),
]


def test_the_clerk_copies_a_printed_invoice_exactly():
    business = simulate(SimConfig(start=date(2025, 12, 1), days=62, seed=11, n_chemists=30))
    truth = max(
        purchase_invoices(business, start=date(2026, 1, 1), end=date(2026, 1, 31)),
        key=lambda invoice: len(invoice.lines),
    )

    def check(invoice):
        return check_invoice(
            invoice,
            items={item.id: item for item in business.catalogue.items},
            parties={party.id: party for party in business.catalogue.companies},
            batches=business.batches,
            received=printed_date(truth.invoice_date),
        )

    extraction = asyncio.run(extract_invoice(types.Part(text=render_text(truth)), check=check))
    assert extraction.check is not None and extraction.check.ready, extraction.check
    fields = ("batch_no", "expiry", "quantity", "mrp", "rate", "taxable_value", "tax_amount")
    read = [tuple(getattr(line, f) for f in fields) for line in extraction.invoice.lines]
    assert read == [tuple(getattr(line, f) for f in fields) for line in truth.lines]
    assert extraction.invoice.grand_total == truth.grand_total
