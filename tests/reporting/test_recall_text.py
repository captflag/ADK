from datetime import timedelta

from batchward.compliance.recall import open_recall
from batchward.compliance.recall_report import recall_report
from batchward.core.holds import HoldLog
from batchward.core.ledger import Ledger
from batchward.core.models import MovementType, Party, PartyKind
from batchward.reporting.recall import format_moment, render_recall_report
from factories import at, movement
from recall_factories import ITEMS, NOTICE, PARTIES, RECALLED

CHEMISTS = {
    "CH1": Party(
        id="CH1",
        kind=PartyKind.CHEMIST,
        name="Jain Medical, Sadar",
        drug_licence_no="NGP/20B/11111",
    ),
    "CH2": Party(
        id="CH2",
        kind=PartyKind.CHEMIST,
        name="Khan Chemists, Itwari",
        drug_licence_no="NGP/20B/22222",
    ),
}
ALL_PARTIES = PARTIES | CHEMISTS


def render(extra=(), as_of_hours=100):
    ledger = Ledger(
        [
            movement(MovementType.PURCHASE, 1_500, batch=RECALLED, when=at(5)),
            movement(MovementType.SALE, -1_200, batch=RECALLED, when=at(10), party_id="CH1"),
            movement(MovementType.SALE, -40, batch=RECALLED, when=at(11), party_id="CH2"),
        ]
    )
    holds = HoldLog()
    open_recall(
        NOTICE, ledger=ledger, items=ITEMS, parties=ALL_PARTIES, holds=holds, at=NOTICE.received_at
    )
    for m in extra:
        ledger.append(m)
    report = recall_report(
        NOTICE,
        RECALLED,
        ledger=ledger,
        holds=holds,
        as_of=NOTICE.received_at + timedelta(hours=as_of_hours),
    )
    return render_recall_report(report, items=ITEMS, parties=ALL_PARTIES)


def returned(qty, party, hours):
    return movement(
        MovementType.SALE_RETURN,
        qty,
        batch=RECALLED,
        when=NOTICE.received_at + timedelta(hours=hours),
        party_id=party,
        location_id="RETURNS",
    )


def test_identifies_the_notice_product_and_batch():
    text = render()
    assert "RN/2026/014, Class I, from Nilgiri Biotech" in text
    assert "Azinil 500 (Azithromycin 500 mg), strip of 3 tablets" in text
    assert "AZ4021, expiry 10/2027" in text
    assert "Draft for the competent person to check and sign." in text


def test_lists_each_chemist_with_licence_and_uses_indian_digit_grouping():
    text = render([returned(1_200, "CH1", 10)])
    (jain,) = [line for line in text.splitlines() if "Jain Medical, Sadar" in line]
    assert "NGP/20B/11111" in jain
    assert jain.split()[-4:-1] == ["1,200", "1,200", "0"]
    assert "Supplied to 2 chemists" in text


def test_shows_each_deadline_and_its_state():
    text = render([returned(1_200, "CH1", 10)])
    due = format_moment(NOTICE.received_at + timedelta(hours=24))
    assert f"Stop sale             MET       due {due}" in text
    assert "Complete the recall   OVERDUE" in text


def test_calls_out_what_needs_attention():
    sale = movement(
        MovementType.SALE,
        -7,
        batch=RECALLED,
        when=NOTICE.received_at + timedelta(hours=3),
        party_id="CH2",
    )
    text = render([sale])
    assert "Needs attention" in text
    assert f"sold 7 units on bill {sale.document_ref} while the batch was blocked" in text
    # The 7 units sold after the notice have to come back as well.
    assert "1,247 units are still with 2 chemists after the recall deadline" in text


def test_a_clean_recall_needs_no_attention():
    text = render([returned(1_200, "CH1", 10), returned(40, "CH2", 20)])
    assert "Nothing needs attention." in text
    assert "Complete the recall   MET" in text


def test_times_are_shown_in_indian_standard_time():
    assert format_moment(at(12, 9, month=2)) == "12/02/2026 09:00 IST"


def test_a_long_licence_number_and_name_are_printed_in_full(monkeypatch):
    long = Party(
        id="CH1",
        kind=PartyKind.CHEMIST,
        name="Shree Ganesh Medical and General Stores, Sitabuldi Main Road",
        drug_licence_no="KA-B21-20B-123456, KA-B21-21B-123456",
    )
    monkeypatch.setitem(ALL_PARTIES, "CH1", long)
    text = render()
    assert "KA-B21-20B-123456, KA-B21-21B-123456" in text
    assert "Shree Ganesh Medical and General Stores, Sitabuldi Main Road" in text
