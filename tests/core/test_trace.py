from dataclasses import replace

import pytest

from batchward.core.ledger import Ledger
from batchward.core.models import MovementType
from batchward.core.trace import trace_batch
from factories import at, batch_key, movement

PURCHASE = MovementType.PURCHASE
PURCHASE_RETURN = MovementType.PURCHASE_RETURN
SALE = MovementType.SALE
SALE_RETURN = MovementType.SALE_RETURN
TRANSFER_IN = MovementType.TRANSFER_IN
TRANSFER_OUT = MovementType.TRANSFER_OUT
WRITE_OFF = MovementType.WRITE_OFF
ADJUSTMENT = MovementType.ADJUSTMENT

BATCH = batch_key()


def sale(qty, party, day, doc=None):
    m = movement(SALE, -qty, when=at(day), party_id=party)
    return m if doc is None else replace(m, document_ref=doc)


def test_lists_every_chemist_supplied_largest_first():
    ledger = Ledger(
        [
            movement(PURCHASE, 100, when=at(1)),
            sale(10, "CHEM-B", 2),
            sale(25, "CHEM-A", 3),
            sale(10, "CHEM-C", 4),
        ]
    )
    trace = trace_batch(ledger, BATCH)
    assert [(r.party_id, r.units) for r in trace.recipients] == [
        ("CHEM-A", 25),
        ("CHEM-B", 10),
        ("CHEM-C", 10),
    ]
    assert trace.supplied == 45


def test_combines_repeat_supplies_to_one_chemist():
    ledger = Ledger(
        [
            movement(PURCHASE, 100, when=at(1)),
            sale(5, "CHEM-A", 2, doc="INV-101"),
            sale(7, "CHEM-A", 9, doc="INV-188"),
        ]
    )
    (recipient,) = trace_batch(ledger, BATCH).recipients
    assert recipient.units == 12
    assert (recipient.first_supplied, recipient.last_supplied) == (at(2), at(9))
    assert recipient.documents == ("INV-101", "INV-188")


def test_sale_returns_reduce_what_a_chemist_holds():
    ledger = Ledger(
        [
            movement(PURCHASE, 100, when=at(1)),
            sale(10, "CHEM-A", 2),
            movement(SALE_RETURN, 4, when=at(5), party_id="CHEM-A"),
        ]
    )
    (recipient,) = trace_batch(ledger, BATCH).recipients
    assert recipient.units == 6


def test_a_chemist_who_returned_everything_is_not_a_recipient():
    ledger = Ledger(
        [
            movement(PURCHASE, 100, when=at(1)),
            sale(10, "CHEM-A", 2),
            movement(SALE_RETURN, 10, when=at(5), party_id="CHEM-A"),
        ]
    )
    assert trace_batch(ledger, BATCH).recipients == ()


def test_a_reversed_sale_is_not_a_supply():
    wrong = sale(10, "CHEM-A", 2)
    ledger = Ledger([movement(PURCHASE, 100, when=at(1)), wrong])
    ledger.reverse(wrong.id, reversal_id="R1", at=at(3), document_ref="CORR-1")
    trace = trace_batch(ledger, BATCH)
    assert trace.recipients == ()
    assert trace.on_hand == {"GODOWN": 100}


def test_received_is_net_of_purchase_returns_and_ignores_internal_transfers():
    ledger = Ledger(
        [
            movement(PURCHASE, 100, when=at(1)),
            movement(PURCHASE_RETURN, -20, when=at(2)),
            movement(TRANSFER_OUT, -30, when=at(3)),
            movement(TRANSFER_IN, 30, when=at(3), location_id="COLD_ROOM"),
        ]
    )
    trace = trace_batch(ledger, BATCH)
    assert trace.received == 80
    assert trace.on_hand == {"GODOWN": 50, "COLD_ROOM": 30}


def test_sales_without_a_buyer_are_reported_as_untraceable():
    ledger = Ledger([movement(PURCHASE, 100, when=at(1)), movement(SALE, -8, when=at(2))])
    trace = trace_batch(ledger, BATCH)
    assert trace.untraceable == 8
    assert trace.recipients == ()


def test_as_of_shows_the_batch_as_it_stood_on_a_date():
    ledger = Ledger(
        [
            movement(PURCHASE, 100, when=at(1)),
            sale(10, "CHEM-A", 2),
            sale(15, "CHEM-B", 20),
        ]
    )
    trace = trace_batch(ledger, BATCH, as_of=at(10))
    assert [r.party_id for r in trace.recipients] == ["CHEM-A"]
    assert trace.on_hand == {"GODOWN": 90}


def test_an_unknown_batch_traces_to_nothing():
    trace = trace_batch(Ledger([movement(PURCHASE, 5, when=at(1))]), batch_key("NONE"))
    assert (trace.received, trace.recipients, trace.on_hand) == (0, (), {})


@pytest.mark.parametrize("adjust", [-3, 2])
def test_every_unit_is_accounted_for(adjust):
    ledger = Ledger(
        [
            movement(PURCHASE, 200, when=at(1)),
            movement(PURCHASE_RETURN, -10, when=at(2)),
            sale(40, "CHEM-A", 3),
            sale(25, "CHEM-B", 4),
            movement(SALE_RETURN, 5, when=at(5), party_id="CHEM-B"),
            movement(SALE, -6, when=at(6)),
            movement(WRITE_OFF, -12, when=at(7)),
            movement(ADJUSTMENT, adjust, when=at(8)),
            movement(TRANSFER_OUT, -50, when=at(9)),
            movement(TRANSFER_IN, 50, when=at(9), location_id="COLD_ROOM"),
        ]
    )
    t = trace_batch(ledger, BATCH)
    assert t.received - t.supplied - t.untraceable - t.written_off + t.adjusted == sum(
        t.on_hand.values()
    )
