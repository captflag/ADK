"""The transfer voucher Marg imports: one row out of a godown, one row into another."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from batchward.bridge.transfer_voucher import IN, OUT, TRANSFER_COLUMNS, render_transfer
from batchward.compliance.quarantine import GODOWN, Move, Transfer
from batchward.core.models import BatchKey, Item

RECALLED = BatchKey("C01", "I001", "AZ4021", date(2027, 10, 31))
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
TRANSFER = Transfer(
    number="QT/260213",
    godown=GODOWN,
    on=date(2026, 2, 13),
    into=(Move(RECALLED, "MAIN", GODOWN, 120, "H00001", "recalled"),),
    back=(Move(RECALLED, GODOWN, "COLD", 30, "H00002", "the hold has been lifted"),),
)


def rows(transfer: Transfer = TRANSFER, items=None) -> list[list[str]]:
    written = render_transfer(transfer, {"I001": AZINIL} if items is None else items)
    return list(csv.reader(io.StringIO(written)))


def test_the_voucher_names_its_columns_first():
    assert rows()[0] == list(TRANSFER_COLUMNS)


def test_each_move_leaves_one_godown_and_enters_another():
    _, out, into, *_ = rows()
    assert out[2:] == [OUT, "13/02/2026", "I001", "Azinil 500", "AZ4021", "10/2027", "120",
                       "MAIN", "recalled"]  # fmt: skip
    assert into[2] == IN and into[9] == GODOWN
    assert (out[0], out[1], into[1]) == ("QT/260213", "1", "2")


def test_what_comes_back_is_written_the_same_way_round():
    _, _, _, out, into = rows()
    assert (out[2], out[9]) == (OUT, GODOWN)
    assert (into[2], into[9], into[8]) == (IN, "COLD", "30")


def test_every_line_is_numbered_in_order():
    assert [row[1] for row in rows()[1:]] == ["1", "2", "3", "4"]


def test_an_item_the_master_does_not_hold_is_left_unnamed_rather_than_guessed():
    out = rows(items={})[1]
    assert out[4:6] == ["I001", ""]


def test_a_transfer_with_nothing_to_move_is_a_heading_and_no_rows():
    empty = Transfer(number="QT/260213", godown=GODOWN, on=date(2026, 2, 13))
    assert rows(empty) == [list(TRANSFER_COLUMNS)]
