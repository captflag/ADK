import csv
import io
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import partial

import pytest

from batchward.core.approvals import Approval, digest
from batchward.core.orders import OrderLine
from batchward.core.printed import printed_date
from batchward.intake.checks import Severity, check_invoice
from batchward.intake.posting import KIND, post, posting
from batchward.intake.receipt import (
    CountSheetError,
    DebitReason,
    match_receipt,
    read_count_sheet,
)
from batchward.intake.voucher import VOUCHER_COLUMNS, approval_id, debit_note, purchase_voucher
from batchward.records.store import RecordsError, RecordStore
from batchward.sim.business import SimConfig, simulate
from batchward.sim.invoices import count_sheet, purchase_invoices


@pytest.fixture(scope="module")
def business():
    return simulate(SimConfig(start=date(2025, 12, 1), days=62, seed=11, n_chemists=30))


@pytest.fixture(scope="module")
def invoice(business):
    invoices = purchase_invoices(business, start=date(2026, 1, 1), end=date(2026, 1, 31))
    return max(invoices, key=lambda invoice: len(invoice.lines))


def receive(
    business, invoice, *, count=None, order="match", batches=None, received=None, earlier=None
):
    batches = business.batches if batches is None else batches
    check = check_invoice(
        invoice,
        items={item.id: item for item in business.catalogue.items},
        parties={party.id: party for party in business.catalogue.companies},
        batches=batches,
        received=received or printed_date(invoice.invoice_date),
    )
    if order == "match":
        order = next(o for o in business.orders if o.number == invoice.order_no)
    counted = read_count_sheet(io.StringIO(count or count_sheet(business, invoice)))
    return match_receipt(check, order=order, counted=counted, batches=batches, earlier=earlier)


def recount(business, invoice, line, **changes):
    rows = list(csv.DictReader(io.StringIO(count_sheet(business, invoice))))
    rows[line].update(changes)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def problems(receipt, severity):
    return [(f.line, f.field) for f in receipt.findings if f.severity is severity]


def test_a_delivery_counted_in_full_against_its_order_is_ready_to_post(business, invoice):
    receipt = receive(business, invoice)
    assert receipt.ready, receipt.findings
    assert receipt.debit_note == ()
    assert [(r.paid, r.free, r.short) for r in receipt.lines] == [
        (int(line.quantity), 0, 0) for line in invoice.lines
    ]


def test_units_billed_but_not_received_go_on_the_debit_note(business, invoice):
    billed = int(invoice.lines[0].quantity)
    receipt = receive(business, invoice, count=recount(business, invoice, 0, **{
        "Units Counted": str(billed - 7)}))  # fmt: skip
    assert receipt.ready
    (short,) = receipt.debit_note
    rate = Decimal(invoice.lines[0].rate)
    assert (short.units, short.taxable_value) == (7, (7 * rate).quantize(Decimal("0.01")))
    assert short.tax_amount == (short.taxable_value * Decimal("0.05")).quantize(Decimal("0.01"))
    assert receipt.lines[0].paid == billed - 7
    assert (1, "count") in problems(receipt, Severity.NOTE)


def test_units_that_arrived_damaged_are_not_posted_and_go_on_the_debit_note(business, invoice):
    billed = int(invoice.lines[0].quantity)
    count = recount(business, invoice, 0, **{"Units Counted": str(billed), "Damaged": "4"})
    receipt = receive(business, invoice, count=count, earlier={})
    assert receipt.ready, receipt.findings
    (broken,) = receipt.debit_note
    rate = Decimal(invoice.lines[0].rate)
    assert (broken.reason, broken.units) == (DebitReason.DAMAGED, 4)
    assert broken.taxable_value == (4 * rate).quantize(Decimal("0.01"))
    first = receipt.lines[0]
    assert (first.counted, first.paid, first.damaged, first.short) == (billed, billed - 4, 4, 0)
    whole = receive(business, invoice, earlier={})
    item_id = first.invoice_line.item.id
    assert receipt.still_due()[item_id] == whole.still_due()[item_id] + 4
    rows = list(csv.DictReader(io.StringIO(purchase_voucher(receipt))))
    assert rows[0]["QTY"] == str(billed - 4)
    (message,) = [f.message for f in receipt.findings if f.line == 1]
    assert message.startswith("4 billed units of batch") and "arrived damaged" in message


def test_short_and_damaged_units_of_one_batch_are_listed_apart_on_the_debit_note(business, invoice):
    billed = int(invoice.lines[0].quantity)
    count = recount(business, invoice, 0, **{"Units Counted": str(billed - 3), "Damaged": "2"})
    receipt = receive(business, invoice, count=count)
    assert [(line.reason, line.units) for line in receipt.debit_note] == [
        (DebitReason.NOT_RECEIVED, 3),
        (DebitReason.DAMAGED, 2),
    ]
    assert receipt.lines[0].paid == billed - 5
    note = debit_note(receipt, received=date(2026, 1, 31))
    assert "Billed but not received when the delivery was counted on 31/01/2026:" in note
    assert "Billed and received damaged when the delivery was counted on 31/01/2026" in note
    total = note.index("\n  Total ")
    assert note.index("not received") < note.index("received damaged") < total


def test_damaged_units_beyond_those_billed_are_left_to_the_representative(business, invoice):
    lines = [invoice.lines[0].model_copy(update={"free_quantity": "10"}), *invoice.lines[1:]]
    with_free = invoice.model_copy(update={"lines": lines})
    billed = int(invoice.lines[0].quantity)
    count = recount(business, with_free, 0, **{"Units Counted": str(billed + 10),
                                               "Damaged": "12"})  # fmt: skip
    receipt = receive(business, with_free, count=count)
    first = receipt.lines[0]
    assert (first.paid, first.free, first.damaged, first.short) == (billed - 2, 0, 2, 0)
    notes = [f.message for f in receipt.findings if f.line == 1]
    assert any("10 damaged units of batch" in note and "beyond what was billed" in note
               for note in notes)  # fmt: skip


def test_a_discount_on_the_bill_is_taken_off_the_debit_note(business, invoice):
    line = invoice.lines[0]
    discounted = (Decimal(line.taxable_value) * Decimal("0.9")).quantize(Decimal("0.01"))
    lines = [line.model_copy(update={
        "discount_percent": "10", "taxable_value": str(discounted),
        "tax_amount": str((discounted * Decimal("0.05")).quantize(Decimal("0.01"))),
    }), *invoice.lines[1:]]  # fmt: skip
    taxable = sum(Decimal(item.taxable_value) for item in lines)
    tax = sum(Decimal(item.tax_amount) for item in lines)
    grand = (taxable + tax).quantize(Decimal(1))
    billed = invoice.model_copy(update={
        "lines": lines, "taxable_total": str(taxable), "tax_total": str(tax),
        "round_off": str(grand - taxable - tax), "grand_total": str(grand),
    })  # fmt: skip
    count = recount(business, billed, 0, **{"Units Counted": str(int(line.quantity) - 10)})
    (short,) = receive(business, billed, count=count).debit_note
    assert short.taxable_value == (10 * Decimal(line.rate) * Decimal("0.9")).quantize(
        Decimal("0.01")
    )


def test_more_counted_than_billed_is_not_posted_without_a_decision(business, invoice):
    extra = str(int(invoice.lines[0].quantity) + 5)
    receipt = receive(business, invoice, count=recount(business, invoice, 0, **{
        "Units Counted": extra}))  # fmt: skip
    assert (1, "count") in problems(receipt, Severity.ASK)
    assert not receipt.ready
    assert receipt.lines[0].paid == int(invoice.lines[0].quantity)


def test_free_units_that_did_not_arrive_are_noted(business, invoice):
    lines = [invoice.lines[0].model_copy(update={"free_quantity": "10"}), *invoice.lines[1:]]
    with_free = invoice.model_copy(update={"lines": lines})
    receipt = receive(business, with_free)
    assert receipt.ready
    assert (1, "count") in problems(receipt, Severity.NOTE)
    assert (receipt.lines[0].free, receipt.lines[0].short) == (0, 0)


def test_a_batch_not_counted_or_counted_but_not_billed_needs_a_person(business, invoice):
    rows = count_sheet(business, invoice).splitlines()
    without_first = "\n".join([rows[0], *rows[2:], "STRANGER 10'S,ZZ9999,01/28,01/26,5"]) + "\n"
    receipt = receive(business, invoice, count=without_first)
    asks = problems(receipt, Severity.ASK)
    assert (1, "count") in asks
    assert (None, "count") in asks


def test_a_pack_expiry_that_disagrees_with_the_invoice_needs_a_person(business, invoice):
    receipt = receive(business, invoice, count=recount(business, invoice, 0, Expiry="01/29"))
    assert (1, "expiry") in problems(receipt, Severity.ASK)


def test_a_new_batch_takes_its_manufacture_month_from_the_pack(business, invoice):
    first = next(key for key in business.batches if key.batch_no == invoice.lines[0].batch_no)
    batches = {key: batch for key, batch in business.batches.items() if key != first}
    receipt = receive(business, invoice, batches=batches)
    assert receipt.lines[0].manufactured == business.batches[first].manufactured.replace(day=1)
    missing = recount(business, invoice, 0, Mfg="")
    assert (1, "manufactured") in problems(
        receive(business, invoice, count=missing, batches=batches), Severity.ASK
    )


def test_the_order_is_matched_by_product_and_quantity(business, invoice):
    order = next(o for o in business.orders if o.number == invoice.order_no)
    fewer = replace(order, lines=tuple(
        OrderLine(line.item_id, max(1, line.quantity // 2)) for line in order.lines))  # fmt: skip
    assert (1, "quantity") in problems(receive(business, invoice, order=fewer), Severity.ASK)
    first_item = receive(business, invoice).lines[0].invoice_line.item
    others = tuple(line for line in order.lines if line.item_id != first_item.id)
    missing_first = replace(order, lines=others or (OrderLine("NOTHING", 1),))
    receipt = receive(business, invoice, order=missing_first)
    assert (1, "order_no") in problems(receipt, Severity.ASK)
    other = replace(order, number="PO/OTHER/1")
    assert (None, "order_no") in problems(receive(business, invoice, order=other), Severity.ASK)
    assert (None, "order_no") in problems(receive(business, invoice, order=None), Severity.NOTE)


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([], "empty"),
        (["Product,Expiry"], "no column 'Batch No.'"),
        (["Batch No.,Units Counted", "AZ4021,ten"], "line 2 counts 'ten'"),
        (["Batch No.,Units Counted", ",5"], "line 2 has no batch number"),
        (["Batch No.,Units Counted,Damaged", "AZ4021,5,two"], "line 2 has 'two' damaged"),
        (["Batch No.,Units Counted,Damaged", "AZ4021,5,6"], "6 units damaged of only 5 counted"),
    ],
)
def test_a_count_sheet_that_cannot_be_read_is_refused(lines, message):
    with pytest.raises(CountSheetError, match=message):
        read_count_sheet(lines)


def test_a_ready_receipt_becomes_a_marg_voucher_of_what_arrived(business, invoice):
    count = recount(business, invoice, 0, **{"Units Counted": "1"})
    receipt = receive(business, invoice, count=count)
    rows = list(csv.reader(io.StringIO(purchase_voucher(receipt))))
    assert tuple(rows[0]) == VOUCHER_COLUMNS
    assert len(rows) == 1 + len(invoice.lines)
    first = dict(zip(VOUCHER_COLUMNS, rows[1], strict=True))
    assert (first["BILL NO"], first["BATCH"], first["QTY"], first["ORDER NO"]) == (
        invoice.invoice_no,
        invoice.lines[0].batch_no,
        "1",
        invoice.order_no,
    )
    assert first["TAXABLE"] == invoice.lines[0].rate
    note = debit_note(receipt, received=date(2026, 1, 31))
    assert "DEBIT NOTE" in note and "Nothing has been sent" in note
    assert f"invoice {invoice.invoice_no}" in note


def test_a_receipt_not_ready_has_no_voucher_and_approval_ids_are_stable(business, invoice):
    extra = recount(business, invoice, 0, **{"Units Counted": "100000"})
    with pytest.raises(ValueError, match="not ready"):
        purchase_voucher(receive(business, invoice, count=extra))
    assert approval_id(receive(business, invoice).check) == approval_id(
        receive(business, invoice, count=extra).check
    )
    assert debit_note(receive(business, invoice), received=date(2026, 1, 31)) is None


def test_an_order_on_several_bills_counts_what_earlier_bills_received(business, invoice):
    order = next(o for o in business.orders if o.number == invoice.order_no)
    first = receive(business, invoice).lines[0].invoice_line
    billed = receive(business, invoice).received()[first.item.id]
    ordered = order.ordered(first.item.id)

    fits = receive(business, invoice, earlier={first.item.id: ordered - billed})
    assert fits.ready and fits.still_due()[first.item.id] == 0

    again = receive(business, invoice, earlier={first.item.id: ordered})
    assert not again.ready
    (message,) = [f.message for f in again.findings if (f.line, f.field) == (1, "quantity")]
    assert f"and {ordered} already received on earlier bills" in message


def test_what_is_still_due_counts_only_billed_units_that_arrived(business, invoice):
    order = next(o for o in business.orders if o.number == invoice.order_no)
    count = recount(business, invoice, 0, **{"Units Counted": "0"})
    receipt = receive(business, invoice, count=count, earlier={})
    first = receipt.lines[0].invoice_line.item.id
    assert first not in receipt.received()
    assert receipt.still_due()[first] == order.ordered(first)
    assert set(receipt.still_due()) == {line.item_id for line in order.lines}


def test_without_earlier_bills_the_order_is_matched_alone_and_that_is_noted(business, invoice):
    receipt = receive(business, invoice)
    assert receipt.ready and receipt.still_due() is None
    notes = [f.message for f in receipt.findings if f.severity is Severity.NOTE]
    assert any("was not looked up" in message for message in notes)
    assert not any(
        "was not looked up" in f.message for f in receive(business, invoice, earlier={}).findings
    )


def test_posting_refuses_a_changed_receipt_and_a_bill_on_the_order_approved_meanwhile(
    business, invoice, tmp_path
):
    at, received_on = datetime(2026, 1, 31, 10, tzinfo=UTC), date(2026, 1, 31)
    receipt = receive(business, invoice, earlier={})
    planned = posting(receipt, received_on=received_on)
    changed = receive(business, invoice, earlier={}, count=recount(business, invoice, 0, **{
        "Units Counted": "1"}))  # fmt: skip
    post_it = partial(post, received_on=received_on, approved_by="Ravi", at=at, out=tmp_path)
    with RecordStore(tmp_path / "records.sqlite") as store:
        with pytest.raises(RecordsError, match="has changed since it was put up"):
            post_it(store, changed, planned)
        other = Approval("purchase:C01:OTHER", KIND, "Asha", at, digest("x"), "another bill")
        store.save_approval(other)
        store.save_received(other.id, invoice.order_no, {"ITEM-NOT-BILLED-HERE": 1})
        with pytest.raises(RecordsError, match="another bill on order"):
            post_it(store, receipt, planned)
        assert store.approval(planned.approval_id) is None

        again = receive(business, invoice, earlier=store.received_against(invoice.order_no))
        first = post_it(store, again, posting(again, received_on=received_on))
        assert sorted(path.name for path in first.written) == sorted(planned.files)
        assert post_it(store, again, posting(again, received_on=received_on)).written == ()
