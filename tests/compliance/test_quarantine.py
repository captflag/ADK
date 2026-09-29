"""Blocked stock moved out of the godowns Marg bills from, and back when it is released."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from batchward.compliance.quarantine import (
    GODOWN,
    Move,
    QuarantineError,
    Transfer,
    blocked_and_sellable,
    draft_transfer,
    released_in_quarantine,
    sheet,
    transfer_number,
    transfer_posting,
)
from batchward.core.holds import HoldLog
from batchward.core.ledger import Ledger
from batchward.core.models import BatchStatus, Item, Location, MovementType
from factories import at, batch_key, movement

MAIN = Location(id="MAIN", name="Main godown")
COLD = Location(id="COLD", name="Cold room", cold_room=True)
QUARANTINE = Location(id=GODOWN, name="Quarantine", sellable=False)
RETURNS = Location(id="RETURNS", name="Breakage shelf", sellable=False)
HERE = (MAIN, COLD, QUARANTINE, RETURNS)
RECALLED = batch_key("AZ4021")
CLEAN = batch_key("PCM9981", item_id="I002")
TODAY = date(2026, 2, 13)
AZINIL = Item(
    id="I001",
    company_id="C01",
    brand="Azinil 500",
    molecule="Azithromycin",
    strength="500 mg",
    unit="strip of 3 tablets",
    hsn="3004",
    gst_rate=Decimal("0.12"),
    mrp=Decimal(180),
)
ITEMS = {"I001": AZINIL}


def stock(*movements) -> Ledger:
    ledger = Ledger()
    for entry in movements:
        ledger.append(entry)
    return ledger


def bought(units: int, *, batch=RECALLED, location: str = "MAIN") -> object:
    return movement(MovementType.PURCHASE, units, batch=batch, location_id=location, when=at(1))


def held(*batches, log: HoldLog | None = None) -> HoldLog:
    log = log or HoldLog()
    for batch in batches:
        log.place(
            batch,
            BatchStatus.RECALLED,
            at=at(2),
            reason="named in recall notice RN/2026/014",
            reference="RN/2026/014",
            placed_by="system",
        )
    return log


class TestWhatIsStillSellable:
    def test_a_blocked_batch_in_a_selling_godown_is_to_be_moved(self):
        ledger = stock(bought(120), bought(30, location="COLD"))
        (first, second) = blocked_and_sellable(held(RECALLED), ledger, HERE)
        assert (first.from_location, first.units, first.to_location) == ("COLD", 30, GODOWN)
        assert (second.from_location, second.units) == ("MAIN", 120)
        assert first.hold_id == "H00001"
        assert "RN/2026/014" in first.reason

    def test_a_batch_nobody_blocked_is_left_alone(self):
        ledger = stock(bought(120, batch=CLEAN))
        assert blocked_and_sellable(held(RECALLED), ledger, HERE) == ()

    def test_stock_already_in_quarantine_or_on_a_shelf_it_is_not_sold_from_is_left(self):
        ledger = stock(bought(50, location=GODOWN), bought(20, location="RETURNS"))
        assert blocked_and_sellable(held(RECALLED), ledger, HERE) == ()

    def test_a_hold_that_has_been_lifted_moves_nothing(self):
        log = held(RECALLED)
        log.release("H00001", at=at(3), reason="the company withdrew the notice", released_by="R")
        assert blocked_and_sellable(log, stock(bought(120)), HERE) == ()


class TestComingBack:
    def moved(self) -> list[Move]:
        return [Move(RECALLED, "MAIN", GODOWN, 120, "H00001"), Move(RECALLED, "COLD", GODOWN, 30)]

    def test_released_stock_goes_back_to_the_godown_it_came_from(self):
        ledger = stock(bought(150, location=GODOWN))
        back, stranded = released_in_quarantine(HoldLog(), ledger, moved=self.moved())
        assert [(move.to_location, move.units) for move in back] == [("COLD", 30), ("MAIN", 120)]
        assert stranded == ()
        assert all(move.from_location == GODOWN for move in back)
        assert "lifted" in back[0].reason

    def test_only_what_is_still_there_comes_back(self):
        ledger = stock(bought(20, location=GODOWN))
        back, stranded = released_in_quarantine(HoldLog(), ledger, moved=self.moved())
        assert [(move.to_location, move.units) for move in back] == [("COLD", 20)]
        assert stranded == ()

    def test_stock_still_blocked_stays_in_quarantine(self):
        ledger = stock(bought(150, location=GODOWN))
        back, stranded = released_in_quarantine(held(RECALLED), ledger, moved=self.moved())
        assert (back, stranded) == ((), ())

    def test_units_nothing_on_record_accounts_for_are_left_for_a_person(self):
        ledger = stock(bought(150, location=GODOWN))
        back, stranded = released_in_quarantine(HoldLog(), ledger, moved=[])
        assert back == ()
        (left,) = stranded
        assert (left.units, left.to_location) == (150, "?")
        assert "nothing on record" in left.reason


class TestDrafting:
    def test_a_transfer_is_numbered_by_the_day_so_drafting_twice_drafts_the_same_one(self):
        assert transfer_number(TODAY) == "QT/260213"

    def test_it_gathers_what_goes_out_and_what_comes_back(self):
        ledger = stock(bought(120), bought(40, batch=CLEAN, location=GODOWN))
        moved = [Move(CLEAN, "MAIN", GODOWN, 40, "H00002")]
        transfer = draft_transfer(held(RECALLED), ledger, HERE, on=TODAY, moved=moved)
        assert transfer.number == "QT/260213"
        assert [move.units for move in transfer.into] == [120]
        assert [move.units for move in transfer.back] == [40]
        assert transfer.units == 160
        assert transfer.batches == (RECALLED, CLEAN)
        assert not transfer.empty

    def test_a_godown_stock_is_sold_from_is_refused_as_the_quarantine(self):
        with pytest.raises(QuarantineError, match="not known to be a godown"):
            draft_transfer(HoldLog(), Ledger(), HERE, on=TODAY, godown="MAIN")

    def test_a_godown_nobody_has_said_is_unsellable_is_refused(self):
        with pytest.raises(QuarantineError, match="BATCHWARD_UNSELLABLE_LOCATIONS"):
            draft_transfer(HoldLog(), Ledger(), HERE, on=TODAY, godown="NOWHERE")

    def test_with_nothing_to_move_it_is_empty(self):
        transfer = draft_transfer(HoldLog(), Ledger(), HERE, on=TODAY)
        assert transfer.empty and transfer.units == 0
        assert transfer.summary().endswith("nothing to move (0 batches)")


class TestWhatApprovingItWrites:
    def transfer(self) -> Transfer:
        return draft_transfer(held(RECALLED), stock(bought(120)), HERE, on=TODAY)

    def test_the_posting_names_the_files_and_sums_it_up(self):
        posting = transfer_posting(self.transfer(), ITEMS, voucher="VNO,LINE\n")
        assert posting.approval_id == "quarantine:QT/260213"
        assert sorted(posting.files) == ["QT-260213.marg-transfer.csv", "QT-260213.quarantine.txt"]
        assert posting.summary == "QT/260213: 120 units to QUAR (1 batch)"
        assert len(posting.digest) == 64

    def test_the_digest_follows_what_would_be_written(self):
        first = transfer_posting(self.transfer(), ITEMS, voucher="one")
        again = transfer_posting(self.transfer(), ITEMS, voucher="one")
        changed = transfer_posting(self.transfer(), ITEMS, voucher="another")
        assert first.digest == again.digest
        assert first.digest != changed.digest

    def test_there_is_nothing_to_approve_when_there_is_nothing_to_move(self):
        with pytest.raises(QuarantineError, match="nothing to move"):
            transfer_posting(draft_transfer(HoldLog(), Ledger(), HERE, on=TODAY), ITEMS)

    def test_the_sheet_says_what_moves_and_that_marg_has_not_moved_it(self):
        written = sheet(self.transfer(), ITEMS)
        assert "QUARANTINE TRANSFER QT/260213" in written
        assert "120  Azinil 500 batch AZ4021 expiry 10/2027  MAIN -> QUAR" in written
        assert "RN/2026/014" in written
        assert written.endswith("Marg does not act on this until the transfer voucher is imported.")

    def test_the_sheet_names_what_is_left_for_a_person_to_place(self):
        ledger = stock(bought(150, location=GODOWN))
        transfer = draft_transfer(HoldLog(), ledger, HERE, on=TODAY)
        assert "Left where it is, for a person to place:" in sheet(transfer, ITEMS)


def test_a_move_must_go_somewhere_and_move_something():
    with pytest.raises(QuarantineError, match="at least one unit"):
        Move(RECALLED, "MAIN", GODOWN, 0)
    with pytest.raises(QuarantineError, match="leave MAIN for somewhere else"):
        Move(RECALLED, "MAIN", "MAIN", 5)


def test_moves_are_drawn_up_in_the_same_order_every_time():
    ledger = stock(bought(1, location="COLD"), bought(2), bought(3, batch=CLEAN))
    log = held(RECALLED, CLEAN)
    first = draft_transfer(log, ledger, HERE, on=TODAY).into
    assert [(move.batch.batch_no, move.from_location) for move in first] == [
        ("AZ4021", "COLD"),
        ("AZ4021", "MAIN"),
        ("PCM9981", "MAIN"),
    ]


def test_a_transfer_may_be_numbered_under_another_prefix():
    assert transfer_number(date(2026, 12, 31), prefix="QX") == "QX/261231"
