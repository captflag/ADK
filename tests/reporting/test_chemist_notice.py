from dataclasses import replace
from datetime import timedelta

from batchward.compliance.recall import open_recall
from batchward.compliance.recall_report import recall_report
from batchward.core.holds import HoldLog
from batchward.core.ledger import Ledger
from batchward.core.models import MovementType, Party, PartyKind
from batchward.reporting.chemist_notice import SENDER_PLACEHOLDER, chemist_notices
from factories import at, movement
from recall_factories import ITEMS, NOTICE, PARTIES, RECALLED

PURCHASE, SALE, SALE_RETURN = MovementType.PURCHASE, MovementType.SALE, MovementType.SALE_RETURN
CHEMISTS = {
    party_id: Party(
        id=party_id,
        kind=PartyKind.CHEMIST,
        name=name,
        drug_licence_no=f"NGP/20B/{party_id[-1] * 5}",
        address=f"{name.split(', ')[1]}, Nagpur, Maharashtra",
    )
    for party_id, name in (
        ("CH1", "Jain Medical, Sadar"),
        ("CH2", "Khan Chemists, Itwari"),
        ("CH3", "Joshi Pharmacy, Dharampeth"),
    )
}
ALL_PARTIES = PARTIES | CHEMISTS


def billed(kind, qty, day, party, document, **kwargs):
    return replace(
        movement(kind, qty, batch=RECALLED, when=at(day, month=2), party_id=party, **kwargs),
        document_ref=document,
    )


def ledger():
    """CH1 on two bills, CH2 on one it returns in full, CH3 on one it half returned early."""
    return Ledger(
        [
            movement(PURCHASE, 200, batch=RECALLED, when=at(5)),
            billed(SALE, -20, 1, "CH1", "INV-0101"),
            billed(SALE, -12, 3, "CH1", "INV-0103"),
            billed(SALE, -15, 2, "CH2", "INV-0102"),
            billed(SALE, -10, 4, "CH3", "INV-0104"),
            billed(SALE_RETURN, 4, 6, "CH3", "CN-0106", location_id="RETURNS"),
        ]
    )


def notices(stock=None, *, after_hours=10, **kwargs):
    stock = stock or ledger()
    holds = HoldLog()
    open_recall(
        NOTICE, ledger=stock, items=ITEMS, parties=ALL_PARTIES, holds=holds, at=NOTICE.received_at
    )
    stock.append(
        movement(
            SALE_RETURN,
            15,
            batch=RECALLED,
            when=NOTICE.received_at + timedelta(hours=2),
            party_id="CH2",
            location_id="RETURNS",
        )
    )
    report = recall_report(
        NOTICE,
        RECALLED,
        ledger=stock,
        holds=holds,
        as_of=NOTICE.received_at + timedelta(hours=after_hours),
    )
    return chemist_notices(report, ledger=stock, items=ITEMS, parties=ALL_PARTIES, **kwargs)


def test_only_chemists_with_units_still_to_return_get_a_notice():
    assert sorted(notices()) == ["CH1", "CH3"]


def test_a_notice_names_the_chemist_the_batch_and_every_bill_it_went_on():
    text = notices()["CH1"]
    assert "Draft for the competent person to check and sign. Nothing has been sent." in text
    assert "To        Jain Medical, Sadar" in text
    assert "Address   Sadar, Nagpur, Maharashtra" in text
    assert "Licence   NGP/20B/11111" in text
    assert "Recall of Azinil 500 (Azithromycin 500 mg), strip of 3 tablets" in text
    assert "Batch AZ4021, expiry 10/2027, manufactured by Nilgiri Biotech" in text
    assert "Class I recall notice RN/2026/014 from Nilgiri Biotech" in text
    assert f"  Bill {'INV-0101':24}01/02/2026  20 units" in text
    assert f"  Bill {'INV-0103':24}03/02/2026  12 units" in text
    assert f"  {'Still to return':41}32 units" in text
    assert "Signature of the competent person" in text


def test_units_returned_before_the_notice_are_shown_so_the_figures_add_up():
    text = notices()["CH3"]
    assert f"  Bill {'INV-0104':24}04/02/2026  10 units" in text
    assert f"  {'Returned to us before the notice':41}4 units" in text
    assert f"  {'Still to return':41}6 units" in text


def test_a_reversed_sale_line_is_left_off_the_bill():
    stock = ledger()
    wrong = billed(SALE, -5, 7, "CH1", "INV-0107")
    stock.append(wrong)
    stock.reverse(wrong.id, reversal_id="R1", at=at(8, month=2), document_ref="INV-0107-X")
    assert "INV-0107" not in notices(stock)["CH1"]


def test_the_sender_is_left_for_the_stockist_to_fill_unless_given():
    assert f"From      {SENDER_PLACEHOLDER}" in notices()["CH1"]
    sender = "Godavari Pharma Distributors, Itwari, Nagpur, NGP/20B/99999"
    assert f"From      {sender}" in notices(sender=sender)["CH1"]


def test_the_deadline_is_given_until_it_passes_then_the_return_is_asked_for_at_once():
    assert "3. Return them to us by 15/02/2026 09:00 IST." in notices(after_hours=10)["CH1"]
    late = notices(after_hours=80)["CH1"]
    assert "3. Return them to us at once. The recall was due to be complete by 15/02/2026" in late
