from dataclasses import replace
from datetime import date

import pytest

from batchward.compliance.registrar import Gap, Record, keep_from, rule65_check
from batchward.core.ledger import Ledger
from batchward.core.models import MovementType, Party, PartyKind
from factories import at, batch_key, movement
from recall_factories import ITEMS

COMPANY = Party(
    id="C01",
    kind=PartyKind.COMPANY,
    name="Nilgiri Biotech",
    drug_licence_no="SIM/MFG/001",
    address="Plot 1, Industrial Area, Baddi, Himachal Pradesh",
)
CHEMIST = Party(
    id="CH1",
    kind=PartyKind.CHEMIST,
    name="Jain Medical, Sadar",
    drug_licence_no="NGP/20B/11111",
    address="Sadar, Nagpur, Maharashtra",
)
PARTIES = {p.id: p for p in (COMPANY, CHEMIST)}
BATCH = batch_key("AZ4021", company_id="C01", item_id="I001")


def bill(kind, qty, document, day=10, party="CH1", batch=BATCH, month=1, year=2026, hour=10):
    when = at(day, hour, month=month, year=year)
    return replace(
        movement(kind, qty, batch=batch, when=when, party_id=party), document_ref=document
    )


def purchase(document="PO-0001", day=5, party="C01", **kwargs):
    return bill(MovementType.PURCHASE, 500, document, day=day, party=party, **kwargs)


def sale(document, day=10, party="CH1", qty=-5, **kwargs):
    return bill(MovementType.SALE, qty, document, day=day, party=party, **kwargs)


def check(*moves, parties=PARTIES, since=date(2026, 1, 1), as_of=date(2026, 1, 31)):
    return rule65_check(Ledger(moves), parties, ITEMS, since=since, as_of=as_of)


def gaps(report):
    return [(f.gap, f.document_ref) for f in report.findings]


def test_complete_records_have_no_findings_and_count_memos_and_lines():
    report = check(purchase(), sale("S-0001"), sale("S-0001", qty=-3), sale("S-0002", day=11))
    assert report.complete
    assert (report.sale_memos, report.sale_lines) == (2, 3)
    assert (report.purchase_bills, report.purchase_lines) == (1, 1)


def test_a_sale_with_no_buyer_is_a_memo_the_inspector_cannot_trace():
    report = check(purchase(), sale("S-0001", party=None))
    assert gaps(report) == [(Gap.NO_BUYER, "S-0001")]


@pytest.mark.parametrize(
    ("change", "gap"),
    [
        (dict(drug_licence_no=None), Gap.NO_BUYER_LICENCE),
        (dict(address="  "), Gap.NO_BUYER_ADDRESS),
    ],
)
def test_every_buyer_particular_the_memo_needs_is_checked(change, gap):
    parties = PARTIES | {"CH1": replace(CHEMIST, kind=PartyKind.NURSING_HOME, **change)}
    report = check(purchase(), sale("S-0001"), sale("S-0002", day=12), parties=parties)
    assert gaps(report) == [(gap, "S-0001"), (gap, "S-0002")]


def test_a_buyer_missing_from_the_party_records_is_reported_by_id():
    report = check(purchase(), sale("S-0001", party="CH9"))
    (finding,) = report.findings
    assert (finding.gap, finding.party_id) == (Gap.UNKNOWN_BUYER, "CH9")


@pytest.mark.parametrize(
    ("parties", "party", "gap"),
    [
        (PARTIES, None, Gap.NO_SUPPLIER),
        (PARTIES, "C99", Gap.UNKNOWN_SUPPLIER),
        (PARTIES | {"C01": replace(COMPANY, drug_licence_no="")}, "C01", Gap.NO_SUPPLIER_LICENCE),
        (PARTIES | {"C01": replace(COMPANY, address=None)}, "C01", Gap.NO_SUPPLIER_ADDRESS),
    ],
)
def test_purchase_records_need_the_supplier_licence_and_address(parties, party, gap):
    report = check(purchase(party=party), parties=parties)
    assert gaps(report) == [(gap, "PO-0001")]


def test_a_drug_whose_manufacturer_is_not_on_record_is_reported():
    report = check(purchase(), sale("S-0001"), parties={"CH1": CHEMIST})
    assert (Gap.NO_MANUFACTURER, "S-0001") in gaps(report)
    assert (Gap.UNKNOWN_SUPPLIER, "PO-0001") in gaps(report)


def test_one_memo_for_two_buyers_is_reported():
    other = replace(CHEMIST, id="CH2", name="Khan Chemists, Itwari")
    report = check(
        purchase(), sale("S-0001"), sale("S-0001", party="CH2"), parties=PARTIES | {"CH2": other}
    )
    assert gaps(report) == [(Gap.SEVERAL_PARTIES, "S-0001")]


def test_a_memo_is_one_bill_however_many_of_its_lines_show_a_gap():
    second = batch_key("AZ4022", company_id="C01", item_id="I001")
    report = check(
        purchase(),
        purchase("PO-0002", batch=second),
        sale("S-0001"),
        sale("S-0001", batch=second),
        sale("S-0002", day=11, hour=23),
        sale("S-0002", day=12, hour=0),
        parties={"CH1": CHEMIST},
    )
    memos = [f for f in report.findings if f.document_ref.startswith("S-")]
    assert [(f.gap, f.document_ref, f.day, f.batch) for f in memos] == [
        (Gap.NO_MANUFACTURER, "S-0001", date(2026, 1, 10), BATCH),
        (Gap.NO_MANUFACTURER, "S-0002", date(2026, 1, 11), BATCH),
    ]
    assert report.counts() == {Gap.UNKNOWN_SUPPLIER: 2, Gap.NO_MANUFACTURER: 4}


def test_a_memo_naming_two_unknown_buyers_names_the_first_once():
    report = check(purchase(), sale("S-0001", party="CH9"), sale("S-0001", party="CH8"))
    assert [(f.gap, f.party_id) for f in report.findings] == [
        (Gap.UNKNOWN_BUYER, "CH9"),
        (Gap.SEVERAL_PARTIES, None),
    ]


def test_bills_must_be_numbered_in_date_order_within_their_series():
    report = check(
        purchase(),
        sale("S-0001", day=10),
        sale("S-0003", day=11),
        sale("S-0002", day=12),
        sale("CASH-0001", day=13),
        sale("OPENING", day=14),
    )
    assert gaps(report) == [(Gap.OUT_OF_ORDER, "S-0002")]


def test_a_single_mistyped_bill_number_is_reported_not_the_bills_after_it():
    numbers = ("S-0001", "S-0900", "S-0003", "S-0004", "S-0005", "S-0006")
    report = check(purchase(day=1), *(sale(n, day=day) for day, n in enumerate(numbers, start=2)))
    assert gaps(report) == [(Gap.OUT_OF_ORDER, "S-0900")]


@pytest.mark.parametrize(
    ("year", "last_year"), [("2026", "2025"), ("25-26", "24-25"), ("2025-26", "2024-25")]
)
def test_bill_numbers_ending_in_a_year_are_checked_in_order_within_that_year(year, last_year):
    report = check(
        purchase(),
        sale(f"S/0412/{last_year}", day=9),
        sale(f"S/0003/{year}", day=10),
        sale(f"S/0002/{year}", day=11),
    )
    assert gaps(report) == [(Gap.OUT_OF_ORDER, f"S/0002/{year}")]


def test_memo_numbers_that_restart_each_financial_year_are_different_memos():
    other = replace(CHEMIST, id="CH2", name="Khan Chemists, Itwari")
    moves = (
        purchase(month=3, year=2025),
        sale("S-0001", day=1, month=4, year=2025),
        sale("S-0002", day=18, month=10, year=2025),
        sale("S-0001", day=1, month=4, party="CH2"),
        sale("S-0002", day=2, month=4, party="CH2"),
    )
    parties = PARTIES | {"CH2": other}
    report = check(*moves, parties=parties, since=date(2025, 1, 1), as_of=date(2026, 12, 31))
    assert report.complete
    assert (report.sale_memos, report.sale_lines) == (4, 4)
    later = check(*moves, parties=parties, since=date(2029, 12, 1), as_of=date(2029, 12, 31))
    assert later.memos_past_retention == 4


def test_cancelled_bills_and_bills_outside_the_period_are_not_checked():
    cancelled = sale("S-0005", party=None, day=15)
    ledger = Ledger([purchase(), sale("S-0001"), cancelled, sale("S-0009", party=None, month=2)])
    ledger.reverse(cancelled.id, reversal_id="R1", at=at(16), document_ref="CANCEL")
    report = rule65_check(ledger, PARTIES, ITEMS, since=date(2026, 1, 1), as_of=date(2026, 1, 31))
    assert report.complete
    assert report.sale_memos == 1


def test_memos_must_be_kept_three_years_from_the_date_of_sale():
    assert keep_from(date(2026, 9, 17)) == date(2023, 9, 17)
    assert keep_from(date(2028, 2, 29)) == date(2025, 2, 28)
    report = check(
        purchase(day=1),
        sale("S-0001", day=2),
        sale("S-0002", day=20),
        since=date(2029, 1, 1),
        as_of=date(2029, 1, 10),
    )
    assert report.keep_from == date(2026, 1, 10)
    assert report.memos_past_retention == 1
    assert report.sale_memos == 0


def test_refuses_a_period_that_ends_before_it_starts():
    with pytest.raises(ValueError, match="since must not be after"):
        check(since=date(2026, 2, 1), as_of=date(2026, 1, 1))


def test_counts_findings_by_gap_in_a_stable_order():
    report = check(purchase(party=None), sale("S-0001", party=None), sale("S-0002", party=None))
    assert report.counts() == {Gap.NO_BUYER: 2, Gap.NO_SUPPLIER: 1}


def test_a_sale_memo_and_a_purchase_bill_sharing_a_number_are_told_apart():
    unknown = batch_key("ZZ1001", company_id="C99", item_id="I999")
    report = check(purchase("B-0001", day=10, batch=unknown), sale("B-0001", day=10, batch=unknown))
    assert sorted((f.gap, f.record, f.document_ref) for f in report.findings) == [
        (Gap.NO_MANUFACTURER, Record.PURCHASE_BILL, "B-0001"),
        (Gap.NO_MANUFACTURER, Record.SALE_MEMO, "B-0001"),
    ]
