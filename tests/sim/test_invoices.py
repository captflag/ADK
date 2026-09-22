from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from batchward.core.clock import ist_date
from batchward.core.models import MovementType
from batchward.intake.gstin import gstin_problem
from batchward.sim.business import SimConfig, simulate
from batchward.sim.invoices import pack_label, purchase_invoices

START, END = date(2026, 1, 1), date(2026, 1, 31)


@pytest.fixture(scope="module")
def business():
    return simulate(SimConfig(start=date(2025, 12, 1), days=62, seed=11, n_chemists=30))


@pytest.fixture(scope="module")
def invoices(business):
    return purchase_invoices(business, start=START, end=END)


def test_every_delivery_in_the_period_is_on_exactly_one_invoice_line(business, invoices):
    delivered = Counter(
        (m.batch.batch_no, m.qty)
        for m in business.ledger
        if m.kind is MovementType.PURCHASE
        and not m.document_ref.startswith("OPENING")
        and START <= ist_date(m.at) <= END
    )
    printed = Counter((line.batch_no, int(line.quantity)) for i in invoices for line in i.lines)
    assert printed == delivered


def test_each_company_has_one_invoice_a_day_per_order_numbered_in_order(business, invoices):
    keys = [(i.supplier_name, i.invoice_date, i.order_no) for i in invoices]
    assert len(keys) == len(set(keys))
    assert {i.order_no for i in invoices} <= {order.number for order in business.orders}
    for supplier in {name for name, _, _ in keys}:
        numbers = [i.invoice_no for i in invoices if i.supplier_name == supplier]
        assert numbers == sorted(numbers)


def test_totals_add_up_and_round_to_the_rupee(invoices):
    for invoice in invoices:
        taxable = sum(Decimal(line.taxable_value) for line in invoice.lines)
        tax = sum(Decimal(line.tax_amount) for line in invoice.lines)
        assert Decimal(invoice.taxable_total) == taxable
        assert Decimal(invoice.tax_total) == tax
        grand = Decimal(invoice.grand_total)
        assert grand == grand.to_integral_value()
        assert abs(Decimal(invoice.round_off)) < 1
        assert taxable + tax + Decimal(invoice.round_off) == grand


def test_suppliers_print_a_well_formed_gstin_and_licence(invoices):
    for invoice in invoices:
        assert gstin_problem(invoice.supplier_gstin) is None
        assert invoice.supplier_licence


@pytest.mark.parametrize(
    ("unit", "label"),
    [
        ("strip of 10 tablets", "10'S"),
        ("bottle of 100 tablets", "100'S"),
        ("10 ml vial", "10ML"),
        ("vial", "VIAL"),
        ("strip of 1 tablet", "STRIP OF 1 TABLET"),
    ],
)
def test_packs_are_abbreviated_as_invoices_print_them(unit, label):
    assert pack_label(unit) == label


def test_an_item_never_has_two_batches_with_one_number(business):
    numbers = Counter((key.item_id, key.batch_no) for key in business.batches)
    assert max(numbers.values()) == 1
