from dataclasses import replace
from datetime import timedelta

import pytest

from batchward.compliance.recall import RecallClass, open_recall
from batchward.compliance.recall_report import DeadlineState, recall_report
from batchward.core.holds import HoldLog
from batchward.core.ledger import Ledger
from batchward.core.models import MovementType
from factories import at, movement
from recall_factories import ITEMS, NOTICE, PARTIES, RECALLED

RECEIVED = NOTICE.received_at
PURCHASE, SALE, SALE_RETURN = MovementType.PURCHASE, MovementType.SALE, MovementType.SALE_RETURN


def hours(n):
    return RECEIVED + timedelta(hours=n)


def supplied_ledger():
    """100 received; 30 to CH1, 25 to CH2 on two bills, 10 to CH3 who sent them back early."""
    return Ledger(
        [
            movement(PURCHASE, 100, batch=RECALLED, when=at(5)),
            replace(
                movement(SALE, -30, batch=RECALLED, when=at(10), party_id="CH1"),
                document_ref="INV-1",
            ),
            replace(
                movement(SALE, -20, batch=RECALLED, when=at(11), party_id="CH2"),
                document_ref="INV-2",
            ),
            replace(
                movement(SALE, -5, batch=RECALLED, when=at(20), party_id="CH2"),
                document_ref="INV-3",
            ),
            movement(SALE, -10, batch=RECALLED, when=at(15), party_id="CH3"),
            movement(SALE_RETURN, 10, batch=RECALLED, when=at(25), party_id="CH3"),
        ]
    )


def returned(ledger, qty, party, when):
    ledger.append(
        movement(SALE_RETURN, qty, batch=RECALLED, when=when, party_id=party, location_id="RETURNS")
    )


def report(ledger, holds=None, as_of=None, notice=NOTICE, block_at=None):
    if holds is None:
        holds = HoldLog()
        open_recall(
            notice,
            ledger=ledger,
            items=ITEMS,
            parties=PARTIES,
            holds=holds,
            at=block_at or notice.received_at,
        )
    return recall_report(notice, RECALLED, ledger=ledger, holds=holds, as_of=as_of or hours(100))


def test_the_position_when_the_notice_arrived():
    r = report(supplied_ledger())
    assert r.received == 100
    assert r.on_hand_at_notice == {"GODOWN": 45}
    assert r.untraceable_at_notice == 0
    assert [(c.party_id, c.supplied, c.bills) for c in r.chemists] == [
        ("CH1", 30, ("INV-1",)),
        ("CH2", 25, ("INV-2", "INV-3")),
    ]
    assert r.supplied == 55


def test_reconciles_what_each_chemist_has_returned_since():
    ledger = supplied_ledger()
    returned(ledger, 30, "CH1", hours(10))
    returned(ledger, 20, "CH2", hours(80))
    r = report(ledger)
    assert [(c.party_id, c.recovered, c.outstanding) for c in r.chemists] == [
        ("CH2", 20, 5),
        ("CH1", 30, 0),
    ]
    assert (r.recovered, r.outstanding) == (50, 5)
    assert r.on_hand == {"GODOWN": 45, "RETURNS": 50}
    assert r.chemists[1].last_recovered == hours(10)


def test_the_recall_is_complete_when_every_unit_supplied_is_back():
    ledger = supplied_ledger()
    returned(ledger, 30, "CH1", hours(10))
    returned(ledger, 25, "CH2", hours(60))
    completion = report(ledger).completion
    assert (completion.done_at, completion.state) == (hours(60), DeadlineState.MET)


def test_completion_after_the_deadline_is_met_late():
    ledger = supplied_ledger()
    returned(ledger, 30, "CH1", hours(10))
    returned(ledger, 25, "CH2", hours(90))
    assert report(ledger).completion.state is DeadlineState.MET_LATE


@pytest.mark.parametrize(
    ("as_of", "state"), [(20, DeadlineState.OPEN), (73, DeadlineState.OVERDUE)]
)
def test_an_unfinished_recall_is_open_until_the_deadline_then_overdue(as_of, state):
    ledger = supplied_ledger()
    returned(ledger, 30, "CH1", hours(10))
    r = report(ledger, as_of=hours(as_of))
    assert r.completion.done_at is None
    assert r.completion.due == hours(72)
    assert r.completion.state is state


def test_returns_after_the_report_date_are_not_counted():
    ledger = supplied_ledger()
    returned(ledger, 30, "CH1", hours(50))
    assert report(ledger, as_of=hours(40)).recovered == 0


def test_stop_sale_is_met_by_blocking_on_receipt():
    r = report(supplied_ledger())
    assert r.blocked_at == RECEIVED
    assert r.stop_sale.state is DeadlineState.MET


def test_without_a_block_stop_sale_is_open_then_overdue():
    ledger = supplied_ledger()
    assert report(ledger, holds=HoldLog(), as_of=hours(5)).stop_sale.state is DeadlineState.OPEN
    assert report(ledger, holds=HoldLog(), as_of=hours(30)).stop_sale.state is DeadlineState.OVERDUE


def test_separates_sales_before_the_block_from_sales_while_blocked():
    ledger = supplied_ledger()
    early = movement(SALE, -2, batch=RECALLED, when=hours(1), party_id="CH4")
    late = movement(SALE, -3, batch=RECALLED, when=hours(5), party_id="CH4")
    ledger.append(early)
    ledger.append(late)
    r = report(ledger, block_at=hours(2))
    assert r.blocked_at == hours(2)
    assert r.sales_before_block == (early,)
    assert r.sales_while_blocked == (late,)
    assert r.sold_after_notice == 5


def test_a_reversed_return_is_not_a_recovery_and_a_reversed_sale_is_not_a_sale():
    ledger = supplied_ledger()
    returned(ledger, 30, "CH1", hours(10))
    (credit_note,) = [m for m in ledger if m.kind is SALE_RETURN and m.party_id == "CH1"]
    ledger.reverse(credit_note.id, reversal_id="R1", at=hours(11), document_ref="CN-CANCEL")
    sale = movement(SALE, -2, batch=RECALLED, when=hours(12), party_id="CH4")
    ledger.append(sale)
    ledger.reverse(sale.id, reversal_id="R2", at=hours(13), document_ref="INV-CANCEL")
    r = report(ledger)
    assert r.recovered == 0
    assert r.sold_after_notice == 0


def test_counts_stock_returned_to_the_company_and_written_off_after_the_notice():
    ledger = supplied_ledger()
    ledger.append(movement(MovementType.PURCHASE_RETURN, -40, batch=RECALLED, when=hours(30)))
    ledger.append(movement(MovementType.WRITE_OFF, -5, batch=RECALLED, when=hours(31)))
    r = report(ledger)
    assert (r.returned_to_company, r.written_off) == (40, 5)
    assert r.on_hand == {}


def test_flags_units_sold_without_a_buyer_and_returns_nobody_was_supplied():
    ledger = supplied_ledger()
    ledger.append(movement(SALE, -4, batch=RECALLED, when=at(26)))
    returned(ledger, 6, "CH9", hours(3))
    r = report(ledger)
    assert r.untraceable_at_notice == 4
    stranger = next(c for c in r.chemists if c.party_id == "CH9")
    assert (stranger.supplied, stranger.recovered, stranger.excess) == (0, 6, 6)
    assert r.recovered == 0, "returns beyond what was supplied do not count as recovery"


def test_classes_without_a_stop_sale_limit_have_no_stop_sale_deadline():
    notice = replace(NOTICE, recall_class=RecallClass.II)
    r = report(supplied_ledger(), notice=notice)
    assert r.stop_sale is None
    assert r.completion.due == RECEIVED + timedelta(days=10)


def test_refuses_a_report_dated_before_the_notice():
    with pytest.raises(ValueError, match="before its notice was received"):
        report(supplied_ledger(), as_of=hours(-1))


def everything_returned(ledger):
    returned(ledger, 30, "CH1", hours(10))
    returned(ledger, 25, "CH2", hours(10))


def test_a_reversed_return_undoes_completion():
    ledger = supplied_ledger()
    everything_returned(ledger)
    return_id = next(m.id for m in reversed(list(ledger)) if m.party_id == "CH1")
    ledger.reverse(return_id, reversal_id="R-CH1", at=hours(20), document_ref="CN-X")
    r = report(ledger)
    assert r.outstanding == 30
    assert r.completion.done_at is None
    assert r.completion.state is DeadlineState.OVERDUE


def test_units_sold_after_the_notice_must_come_back_before_the_recall_is_complete():
    ledger = supplied_ledger()
    everything_returned(ledger)
    ledger.append(movement(SALE, -20, batch=RECALLED, when=hours(30), party_id="CH1"))
    r = report(ledger)
    (ch1,) = [c for c in r.chemists if c.party_id == "CH1"]
    assert (ch1.supplied, ch1.supplied_since, ch1.outstanding) == (30, 20, 20)
    assert r.completion.done_at is None
    returned(ledger, 20, "CH1", hours(40))
    r = report(ledger)
    assert (r.outstanding, r.completion.done_at) == (0, hours(40))
    assert not any(c.excess for c in r.chemists)


def test_units_sold_with_no_buyer_keep_the_recall_open():
    ledger = Ledger(
        [
            movement(PURCHASE, 100, batch=RECALLED, when=at(5)),
            movement(SALE, -40, batch=RECALLED, when=at(10)),
        ]
    )
    r = report(ledger)
    assert (r.untraceable_at_notice, r.untraceable) == (40, 40)
    assert r.completion.state is DeadlineState.OVERDUE


def test_a_credit_note_from_before_the_notice_reversed_after_it_is_owed_again():
    ledger = supplied_ledger()
    everything_returned(ledger)
    early_return = next(m for m in ledger if m.kind is SALE_RETURN and m.party_id == "CH3")
    ledger.reverse(early_return.id, reversal_id="R-CN", at=hours(20), document_ref="CN-R")
    r = report(ledger)
    (ch3,) = [c for c in r.chemists if c.party_id == "CH3"]
    assert (ch3.supplied, ch3.recovered, ch3.outstanding) == (10, 0, 10)
    assert r.recovered == 55
    assert r.completion.done_at is None


def test_a_sale_from_before_the_notice_cancelled_after_it_is_not_owed():
    ledger = supplied_ledger()
    returned(ledger, 25, "CH2", hours(10))
    ch1_sale = next(m for m in ledger if m.kind is SALE and m.party_id == "CH1")
    ledger.reverse(ch1_sale.id, reversal_id="R-INV1", at=hours(12), document_ref="INV-1-X")
    r = report(ledger)
    assert r.outstanding == 0
    assert all(c.party_id != "CH1" for c in r.chemists)
    assert r.completion.done_at == hours(12)


def test_a_lifted_block_does_not_count_as_stopping_sale_and_sales_after_it_are_named():
    ledger = supplied_ledger()
    holds = HoldLog()
    (hold,) = open_recall(
        NOTICE, ledger=ledger, items=ITEMS, parties=PARTIES, holds=holds, at=RECEIVED
    ).holds
    holds.release(hold.id, at=hours(1), reason="wrong batch", released_by="owner")
    sale = movement(SALE, -5, batch=RECALLED, when=hours(3), party_id="CH1")
    ledger.append(sale)
    r = report(ledger, holds=holds, as_of=hours(30))
    assert r.blocked_at is None
    assert r.stop_sale.state is DeadlineState.OVERDUE
    assert (r.sales_after_release, r.sales_before_block) == ((sale,), ())
    assert [release.hold_id for release in r.releases] == [hold.id]
    assert r.sold_after_notice == 5


def test_a_block_placed_late_is_met_late():
    r = report(supplied_ledger(), block_at=hours(30))
    assert (r.blocked_at, r.stop_sale.state) == (hours(30), DeadlineState.MET_LATE)


def test_units_transferred_out_with_no_transfer_in_are_reported():
    ledger = supplied_ledger()
    ledger.append(movement(MovementType.TRANSFER_OUT, -20, batch=RECALLED, when=hours(2)))
    r = report(ledger)
    assert r.in_transit == 20
    assert r.on_hand == {"GODOWN": 25}


def test_a_report_as_of_a_past_moment_keeps_a_sale_reversed_only_later():
    ledger = supplied_ledger()
    sale = movement(SALE, -4, batch=RECALLED, when=hours(2), party_id="CH1")
    ledger.append(sale)
    ledger.reverse(sale.id, reversal_id="R-LATE", at=hours(50), document_ref="X")
    assert report(ledger, as_of=hours(10)).sold_after_notice == 4
    assert report(ledger, as_of=hours(60)).sold_after_notice == 0


def test_refuses_a_report_time_with_no_zone():
    with pytest.raises(ValueError, match="timezone-aware"):
        report(supplied_ledger(), as_of=hours(10).replace(tzinfo=None))
