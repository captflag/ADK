"""Supplier invoices for the simulated stockist's deliveries, as they would be printed.

Each company's deliveries against one order on one day become one tax invoice,
quoting the order, with a line per batch, printed the way Indian pharma invoices
print them: the brand in capitals with its pack, expiry as MM/YY, amounts with
two decimals, GST as a percentage, and the total rounded to the rupee. These are
the ground truth for checking intake (ADR 0015): a correct extraction reproduces
them exactly, and a checker must find nothing wrong with them.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from batchward.compliance.nppa import pack_contents
from batchward.core.clock import ist_date
from batchward.core.models import Item, MovementType, Party
from batchward.intake.invoice import InvoiceLine, PurchaseInvoice
from batchward.sim.business import Business

PAISA = Decimal("0.01")


def purchase_invoices(business: Business, *, start: date, end: date) -> list[PurchaseInvoice]:
    """An invoice for each company's deliveries on each day from ``start`` to ``end``."""
    items = {item.id: item for item in business.catalogue.items}
    companies = {company.id: company for company in business.catalogue.companies}
    deliveries = defaultdict(list)
    for m in business.ledger:
        day = ist_date(m.at)
        if (
            m.kind is MovementType.PURCHASE
            and m.party_id in companies
            and m.rate is not None
            and not m.document_ref.startswith("OPENING")
            and start <= day <= end
            and not business.ledger.is_reversed(m.id)
        ):
            deliveries[(day, m.party_id, business.order_of.get(m.id, ""))].append(m)

    invoices = []
    serials: defaultdict[str, int] = defaultdict(int)
    for (day, company_id, order), movements in sorted(deliveries.items()):
        company = companies[company_id]
        serials[company_id] += 1
        lines = [
            _line(items[m.batch.item_id], m.batch.batch_no, m.batch.expiry, m.qty, m.rate,
                  business.batches[m.batch].mrp)
            for m in sorted(movements, key=lambda m: (items[m.batch.item_id].brand, m.batch))
        ]  # fmt: skip
        invoices.append(_invoice(company, day, serials[company_id], order, lines))
    return invoices


def _line(
    item: Item, batch_no: str, expiry: date, quantity: int, rate: Decimal, mrp: Decimal
) -> InvoiceLine:
    taxable = (quantity * rate).quantize(PAISA)
    gst = item.gst_rate * 100
    return InvoiceLine(
        description=f"{item.brand.upper()} {pack_label(item.unit)}",
        hsn=item.hsn,
        batch_no=batch_no,
        expiry=f"{expiry:%m/%y}",
        quantity=str(quantity),
        mrp=f"{mrp:.2f}",
        rate=f"{rate:.2f}",
        gst_percent=f"{gst.normalize():f}",
        taxable_value=f"{taxable:.2f}",
        tax_amount=f"{(taxable * item.gst_rate).quantize(PAISA):.2f}",
    )


def _invoice(
    company: Party, day: date, serial: int, order: str, lines: list[InvoiceLine]
) -> PurchaseInvoice:
    taxable = sum((Decimal(line.taxable_value) for line in lines), Decimal(0))
    tax = sum((Decimal(line.tax_amount) for line in lines), Decimal(0))
    grand = (taxable + tax).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    year = day.year if day.month >= 4 else day.year - 1
    financial_year = f"{year % 100:02d}-{(year + 1) % 100:02d}"
    return PurchaseInvoice(
        supplier_name=company.name,
        supplier_address=company.address or "",
        supplier_gstin=company.gstin or "",
        supplier_licence=company.drug_licence_no or "",
        invoice_no=f"{company.name[:3].upper()}/{financial_year}/{serial:05d}",
        invoice_date=f"{day:%d/%m/%Y}",
        order_no=order,
        lines=lines,
        taxable_total=f"{taxable:.2f}",
        tax_total=f"{tax:.2f}",
        round_off=f"{grand - taxable - tax:.2f}",
        grand_total=f"{grand:.2f}",
    )


def pack_label(unit: str) -> str:
    """A pack as invoices abbreviate it: "strip of 10 tablets" is "10'S", "10 ml vial" is "10ML"."""
    contents = pack_contents(unit)
    if "ml" in contents:
        return f"{contents['ml'].normalize():f}ML"
    counted = [held for held in contents.values() if held != 1]
    return f"{counted[0].normalize():f}'S" if counted else unit.upper()


def render_text(invoice: PurchaseInvoice) -> str:
    """The invoice laid out as plain text, as a printed invoice reads, for extraction to read."""
    rule = "-" * 118
    lines = [
        invoice.supplier_name.upper(),
        invoice.supplier_address,
        f"GSTIN: {invoice.supplier_gstin}    D.L. No.: {invoice.supplier_licence}",
        "TAX INVOICE",
        f"Invoice No.: {invoice.invoice_no}    Date: {invoice.invoice_date}    "
        f"Your Order No.: {invoice.order_no}",
        rule,
        f"{'Sr':<3}{'Product':<24}{'HSN':<6}{'Batch':<9}{'Exp':<7}{'Qty':>6}{'Free':>6}"
        f"{'MRP':>9}{'Rate':>9}{'Disc%':>6}{'GST%':>6}{'Taxable':>12}{'GST Amt':>10}",
        rule,
    ]
    for number, line in enumerate(invoice.lines, start=1):
        lines.append(
            f"{number:<3}{line.description:<24}{line.hsn:<6}{line.batch_no:<9}{line.expiry:<7}"
            f"{line.quantity:>6}{line.free_quantity:>6}{line.mrp:>9}{line.rate:>9}"
            f"{line.discount_percent:>6}{line.gst_percent:>6}{line.taxable_value:>12}"
            f"{line.tax_amount:>10}"
        )
    lines += [
        rule,
        f"{'Taxable Value':>100}{invoice.taxable_total:>18}",
        f"{'GST':>100}{invoice.tax_total:>18}",
        f"{'Round Off':>100}{invoice.round_off:>18}",
        f"{'Grand Total':>100}{invoice.grand_total:>18}",
    ]
    return "\n".join(lines) + "\n"


def count_sheet(business: Business, invoice: PurchaseInvoice) -> str:
    """The godown's count of a delivery as CSV: every unit billed, read off the packs."""
    by_batch = {
        (key.batch_no, key.expiry.year, key.expiry.month): batch
        for key, batch in business.batches.items()
    }
    rows = ["Product,Batch No.,Expiry,Mfg,Units Counted"]
    for line in invoice.lines:
        month, year = (int(part) for part in line.expiry.split("/"))
        batch = by_batch[(line.batch_no, 2000 + year, month)]
        rows.append(
            f"{line.description},{line.batch_no},{line.expiry},"
            f"{batch.manufactured:%m/%y},{line.quantity}"
        )
    return "\n".join(rows) + "\n"
