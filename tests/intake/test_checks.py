from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from batchward.compliance.prices import CeilingPrice, CeilingTable
from batchward.core.clock import IST
from batchward.core.holds import HoldLog
from batchward.core.models import BatchStatus
from batchward.core.printed import printed_date
from batchward.intake.checks import Severity, check_invoice
from batchward.intake.matching import match_item, match_supplier
from batchward.sim.business import SimConfig, simulate
from batchward.sim.invoices import purchase_invoices


@pytest.fixture(scope="module")
def business():
    return simulate(SimConfig(start=date(2025, 11, 1), days=120, seed=7, n_chemists=40))


@pytest.fixture(scope="module")
def invoices(business):
    return purchase_invoices(business, start=date(2026, 1, 1), end=date(2026, 2, 27))


def check(business, invoice, **kwargs):
    kwargs.setdefault("received", printed_date(invoice.invoice_date))
    return check_invoice(
        invoice,
        items={item.id: item for item in business.catalogue.items},
        parties={p.id: p for p in (*business.catalogue.companies, *business.chemists)},
        batches=business.batches,
        ledger=business.ledger,
        **kwargs,
    )


def edited(invoice, line=0, **changes):
    lines = list(invoice.lines)
    lines[line] = lines[line].model_copy(update=changes)
    return invoice.model_copy(update={"lines": lines})


def problems(result, severity):
    return [(f.line, f.field) for f in result.findings if f.severity is severity]


def test_every_invoice_as_printed_is_ready_to_post(business, invoices):
    assert len(invoices) > 100
    for invoice in invoices:
        result = check(business, invoice)
        assert result.ready, (invoice.invoice_no, result.findings)
        assert len(result.lines) == len(invoice.lines)


def test_a_ready_invoice_gives_each_line_its_item_batch_and_figures(business, invoices):
    invoice = invoices[0]
    result = check(business, invoice)
    (first, *_) = result.lines
    printed = invoice.lines[0]
    assert first.item.brand.upper() in printed.description
    assert (first.batch.batch_no, first.quantity, str(first.rate)) == (
        printed.batch_no,
        int(printed.quantity),
        printed.rate,
    )
    assert first.batch in business.batches
    assert result.supplier.name == invoice.supplier_name


class TestMisreadsGoBackToExtraction:
    @pytest.mark.parametrize(
        ("changes", "field"),
        [
            (dict(quantity="5"), "taxable_value"),
            (dict(rate="0.00"), "taxable_value"),
            (dict(tax_amount="13.21"), "tax_amount"),
            (dict(expiry="13/27"), "expiry"),
            (dict(mrp="7O.00"), "mrp"),
            (dict(gst_percent="7"), "gst_percent"),
            (dict(batch_no=""), "batch_no"),
        ],
    )
    def test_a_misread_line_figure_is_sent_back(self, business, invoices, changes, field):
        result = check(business, edited(invoices[0], **changes))
        assert (1, field) in problems(result, Severity.FIX)
        assert not result.ready
        assert any(hint.startswith(f"line 1, {field}: ") for hint in result.hints())

    def test_rate_and_mrp_read_into_each_others_columns(self, business, invoices):
        line = invoices[0].lines[0]
        result = check(business, edited(invoices[0], mrp=line.rate, rate=line.mrp))
        assert (1, "rate") in problems(result, Severity.FIX)

    def test_a_misread_expiry_of_a_batch_already_held(self, business, invoices):
        line = invoices[0].lines[0]
        month, year = line.expiry.split("/")
        other = f"{int(month) % 12 + 1:02d}/{year}"
        result = check(business, edited(invoices[0], expiry=other))
        assert (1, "expiry") in problems(result, Severity.FIX)

    def test_a_misread_character_in_the_supplier_gstin(self, business, invoices):
        gstin = invoices[0].supplier_gstin
        misread = gstin[:5] + ("B" if gstin[5] != "B" else "C") + gstin[6:]
        result = check(business, invoices[0].model_copy(update={"supplier_gstin": misread}))
        assert (None, "supplier_gstin") in problems(result, Severity.FIX)

    @pytest.mark.parametrize(
        ("field", "value"),
        [("grand_total", "1.00"), ("taxable_total", "9,99,999.00"), ("round_off", "5.00")],
    )
    def test_totals_that_do_not_add_up(self, business, invoices, field, value):
        result = check(business, invoices[0].model_copy(update={field: value}))
        assert (None, field) in problems(result, Severity.FIX)

    def test_an_invoice_dated_after_it_arrived(self, business, invoices):
        invoice = invoices[0]
        result = check(
            business, invoice, received=printed_date(invoice.invoice_date) - timedelta(1)
        )
        assert (None, "invoice_date") in problems(result, Severity.FIX)


class TestAPersonDecides:
    def test_a_batch_number_that_could_be_one_already_held(self, business, invoices):
        invoice = invoices[0]
        line = invoice.lines[0]
        held = line.batch_no
        letters = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"}
        position = next(i for i, character in enumerate(held) if character in letters)
        misread = held[:position] + letters[held[position]] + held[position + 1 :]
        result = check(business, edited(invoice, batch_no=misread))
        asks = [f for f in result.findings if f.severity is Severity.ASK]
        assert any(f.field == "batch_no" and f"{misread} or {held}?" in f.message for f in asks)

    def test_a_product_no_item_matches(self, business, invoices):
        result = check(business, edited(invoices[0], description="UNOBTAINIUM 99 10'S"))
        assert (1, "description") in problems(result, Severity.ASK)
        assert 1 not in {line.number for line in result.lines}

    def test_the_same_batch_billed_twice(self, business, invoices):
        invoice = invoices[0]
        doubled = invoice.model_copy(update={"lines": [invoice.lines[0], invoice.lines[0]]})
        result = check(business, doubled)
        assert (2, "batch_no") in problems(result, Severity.ASK)

    def test_stock_that_expired_before_the_invoice(self, business, invoices):
        invoice = invoices[0]
        line = invoice.lines[0]
        day = printed_date(invoice.invoice_date)
        expired = f"{(day.month - 2) % 12 + 1:02d}/{day.year % 100 - (day.month <= 1):02d}"
        unknown = edited(invoice, batch_no=line.batch_no + "X9", expiry=expired)
        assert (1, "expiry") in problems(check(business, unknown), Severity.ASK)

    def test_a_batch_blocked_by_a_recall(self, business, invoices):
        invoice = invoices[0]
        result = check(business, invoice)
        batch = result.lines[0].batch
        holds = HoldLog()
        holds.place(
            batch,
            BatchStatus.BLOCKED,
            at=datetime(2025, 12, 1, tzinfo=IST),
            reason="Class I recall",
            reference="RN/TEST",
            placed_by="system",
        )
        blocked = check(business, invoice, holds=holds)
        assert any(f.severity is Severity.ASK and "RN/TEST" in f.message for f in blocked.findings)

    def test_stock_printed_above_its_ceiling(self, business, invoices):
        items = {item.id: item for item in business.catalogue.items}
        invoice, line_result = next(
            (invoice, line)
            for invoice in invoices
            for line in check(business, invoice).lines
            if items[line.item.id].dpco_scheduled
        )
        item = line_result.item
        ceiling = CeilingPrice(
            item.molecule, item.strength, item.unit, Decimal("1.00"), date(2023, 4, 1), "TEST/1"
        )
        result = check(business, invoice, ceilings=CeilingTable([ceiling]))
        assert (line_result.number, "mrp") in problems(result, Severity.ASK)

    def test_a_supplier_not_on_record(self, business, invoices):
        stranger = invoices[0].model_copy(
            update={"supplier_name": "Unknown Traders", "supplier_gstin": "27AAPFU0939F1ZV"}
        )
        assert (None, "supplier_name") in problems(check(business, stranger), Severity.ASK)


def test_a_rate_well_off_the_usual_is_noted_not_refused(business, invoices):
    invoice = invoices[0]
    line = invoice.lines[0]
    rate = (Decimal(line.rate) * Decimal("1.3")).quantize(Decimal("0.01"))
    taxable = (int(line.quantity) * rate).quantize(Decimal("0.01"))
    tax = (taxable * Decimal("0.05")).quantize(Decimal("0.01"))
    dearer = edited(invoice, rate=str(rate), taxable_value=str(taxable), tax_amount=str(tax))
    rest = dearer.lines[1:]
    taxable_total = taxable + sum((Decimal(other.taxable_value) for other in rest), Decimal(0))
    tax_total = tax + sum((Decimal(other.tax_amount) for other in rest), Decimal(0))
    dearer = dearer.model_copy(
        update={
            "taxable_total": str(taxable_total),
            "tax_total": str(tax_total),
            "round_off": "0.00",
            "grand_total": str(taxable_total + tax_total),
        }
    )
    result = check(business, dearer)
    assert (1, "rate") in problems(result, Severity.NOTE)
    assert result.ready


class TestMatching:
    def test_a_supplier_is_found_by_gstin_then_by_name(self, business):
        company = business.catalogue.companies[0]
        parties = business.catalogue.companies
        assert match_supplier("anything", company.gstin, parties) == company
        assert match_supplier(f"M/s. {company.name} Pvt. Ltd.", "", parties) == company
        assert match_supplier("Nobody Pharma", "", parties) is None

    def test_an_item_is_matched_by_its_brand_printed_whole(self, business):
        item = business.catalogue.items[0]
        items = business.catalogue.items
        assert match_item(f"{item.brand.upper()} TAB", items, company_id=None).item == item
        assert match_item(item.brand.replace(" ", ""), items, company_id=None).item == item

    def test_a_brand_in_several_packs_is_settled_by_the_pack_printed(self, business):
        item = next(i for i in business.catalogue.items if "strip of 10" in i.unit)
        other = replace(item, id="X-15", unit="strip of 15 tablets")
        items = [item, other]
        assert match_item(f"{item.brand} 15'S", items, company_id=None).item == other
        ambiguous = match_item(item.brand, items, company_id=None)
        assert (ambiguous.item, len(ambiguous.candidates)) == (None, 2)
