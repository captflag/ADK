from datetime import date
from decimal import Decimal

import pytest

from batchward.claims.terms import ReturnTerms, TermsFileError, TermsTable, read_terms
from batchward.claims.windows import WindowState, claim_windows, lost_claims
from batchward.core.ledger import Ledger
from batchward.core.models import Location, MovementType
from factories import at, batch_key, movement

GODOWN = Location(id="GODOWN", name="Main godown")
RETURNS = Location(id="RETURNS", name="Expiry shelf", sellable=False)
ON = date(2026, 3, 1)


def terms(company="C01", opens=90, closes=30, credit="100", effective=date(2025, 1, 1)):
    return ReturnTerms(company, opens, closes, Decimal(credit), effective, "letter of 2025")


def held(*entries):
    """A ledger holding (key, units, location) entries bought before ON."""
    return Ledger(
        movement(MovementType.PURCHASE, units, when=at(1, month=1), batch=key, location_id=place)
        for key, units, place in entries
    )


def windows(ledger, table, *, costs=None, rates=None, claimed=None, on=ON):
    return claim_windows(
        ledger, costs or {}, [GODOWN, RETURNS], rates or {}, table, on=on, claimed=claimed
    )


def test_a_window_runs_from_days_before_expiry_to_days_after():
    assert terms().window(date(2026, 6, 30)) == (date(2026, 4, 1), date(2026, 7, 30))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"opens": -1}, "cannot open after expiry"),
        ({"closes": -5}, "cannot open after expiry"),
        ({"credit": "0"}, "more than 0%"),
        ({"credit": "101"}, "at most 100%"),
        ({"company": " "}, "a company and a reference"),
    ],
)
def test_terms_that_make_no_sense_are_refused(change, message):
    with pytest.raises(ValueError, match=message):
        terms(**change)


def test_the_terms_in_force_are_the_latest_to_take_effect():
    old, new = terms(), terms(opens=60, effective=date(2026, 2, 1))
    table = TermsTable([new, old])
    assert table.in_force("C01", date(2024, 12, 31)) is None
    assert table.in_force("C01", date(2026, 1, 31)) == old
    assert table.in_force("C01", date(2026, 2, 1)) == new
    assert table.in_force("C99", ON) is None
    with pytest.raises(ValueError, match="take effect on 2025-01-01"):
        TermsTable([old, terms(credit="90")])


def test_terms_read_from_a_file_as_a_stockist_keeps_them():
    lines = [
        "Company,Opens Days Before Expiry,Closes Days After Expiry,Credit %,Effective From,"
        "Reference",
        "C01,90,30,100%,01/04/2025,Area manager letter",
        "",
        "C02,180,0,75,2025-04-01,Circular 12",
    ]
    first, second = read_terms(lines)
    assert first == ReturnTerms(
        "C01", 90, 30, Decimal(100), date(2025, 4, 1), "Area manager letter"
    )
    assert (second.company_id, second.credit_percent, second.closes_days_after_expiry) == (
        "C02",
        Decimal(75),
        0,
    )


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([], "empty"),
        (["Company,Credit"], "no column"),
        (
            ["Company,Opens,Closes,Credit,From,Reference", "C01,90,30,100,someday,x"],
            "line 2: 'someday' is not a date",
        ),
        (["Company,Opens,Closes,Credit,From,Reference", "C01,ninety,30,100,1/4/25,x"], "line 2"),
        (["Company,Opens,Closes,Credit,From,Reference", "C01,90,30,120,1/4/25,x"], "line 2"),
    ],
)
def test_a_terms_file_that_cannot_be_read_is_refused(lines, message):
    with pytest.raises(TermsFileError, match=message):
        read_terms(lines)


def test_each_batch_is_in_the_state_its_window_gives_it():
    later = batch_key("LATER", expiry=date(2026, 12, 31))
    open_ = batch_key("OPEN", expiry=date(2026, 4, 30))
    closing = batch_key("CLOSING", expiry=date(2026, 2, 10))
    closed = batch_key("CLOSED", expiry=date(2026, 1, 15))
    stranger = batch_key("STRANGER", company_id="C02", expiry=date(2026, 4, 30))
    ledger = held(*((key, 10, "RETURNS") for key in (later, open_, closing, closed, stranger)))
    states = {w.batch.batch_no: w.state for w in windows(ledger, TermsTable([terms()]))}
    assert states == {
        "LATER": WindowState.NOT_YET_OPEN,
        "OPEN": WindowState.OPEN,
        "CLOSING": WindowState.CLOSING,  # closes 12/03, 11 days away
        "CLOSED": WindowState.CLOSED,  # closed 14/02
        "STRANGER": WindowState.NO_TERMS,
    }


def test_only_stock_that_will_not_sell_is_claimed_returns_shelf_first():
    key = batch_key(expiry=date(2026, 4, 30))
    ledger = held((key, 100, "GODOWN"), (key, 8, "RETURNS"))
    table = TermsTable([terms(credit="90")])
    (window,) = windows(ledger, table, costs={key: Decimal("20.00")}, rates={"I001": 1.0})
    # 60 days to expiry sell 60 of the 100 in the godown; 40 and the 8 returned will not sell.
    assert window.by_location == (("RETURNS", 8), ("GODOWN", 40))
    assert window.units_to_claim == 48
    assert window.claim_value == Decimal("864.00")  # 48 x 20 x 90%


def test_units_already_claimed_are_not_claimed_again():
    key = batch_key(expiry=date(2026, 4, 30))
    ledger = held((key, 8, "RETURNS"))
    table = TermsTable([terms()])
    (window,) = windows(ledger, table, claimed={(key, "RETURNS"): 5})
    assert window.units_to_claim == 3
    assert windows(ledger, table, claimed={(key, "RETURNS"): 8}) == []


def test_closing_windows_come_first_then_open_ones_by_value():
    small = batch_key("SMALL", expiry=date(2026, 4, 30))
    large = batch_key("LARGE", expiry=date(2026, 5, 15))
    soon = batch_key("SOON", expiry=date(2026, 2, 10))
    ledger = held((small, 1, "RETURNS"), (large, 50, "RETURNS"), (soon, 1, "RETURNS"))
    costs = {small: Decimal(1), large: Decimal(1), soon: Decimal(1)}
    order = [w.batch.batch_no for w in windows(ledger, TermsTable([terms()]), costs=costs)]
    assert order == ["SOON", "LARGE", "SMALL"]


def test_stock_written_off_while_its_window_was_open_is_a_claim_lost():
    inside = batch_key("INSIDE", expiry=date(2026, 2, 28))
    early = batch_key("EARLY", expiry=date(2026, 12, 31))
    ledger = held((inside, 30, "GODOWN"), (early, 30, "GODOWN"))
    ledger.append(movement(MovementType.WRITE_OFF, -30, when=at(28, month=2), batch=inside))
    ledger.append(movement(MovementType.WRITE_OFF, -30, when=at(10, month=2), batch=early))
    costs = {inside: Decimal("10.00"), early: Decimal("10.00")}
    (lost,) = lost_claims(
        ledger,
        costs,
        TermsTable([terms(credit="75")]),
        since=at(1, month=2),
        until=at(1, month=3),
    )
    assert (lost.batch, lost.units, lost.value) == (inside, 30, Decimal("225.00"))
    assert lost.closes == date(2026, 3, 30)
    assert lost.written_off_on == date(2026, 2, 28)
