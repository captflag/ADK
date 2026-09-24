import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from batchward.agents.data import StockData
from batchward.analysis.costs import batch_costs
from batchward.claims.breakage import (
    KIND,
    Breakage,
    Reason,
    ReturnReason,
    breakage_in_stock,
    draft_breakage,
    normalise,
)
from batchward.claims.claim import claim_posting
from batchward.claims.submission import windows_for
from batchward.claims.terms import ReturnTerms, TermsTable
from batchward.core.ledger import Ledger
from batchward.core.models import Item, Location, MovementType, Party, PartyKind
from batchward.records.store import MIGRATIONS, RecordsError, RecordStore
from factories import at, batch_key, movement

ON = date(2026, 3, 1)
GODOWN = Location("GODOWN", "Godown")
RETURNS = Location("RETURNS", "Breakage and expiry shelf", sellable=False)
COMPANY = Party("C01", PartyKind.COMPANY, "Aravalli Pharma")
KEY = batch_key(item_id="I001", expiry=date(2028, 12, 31))
TERMS = TermsTable([ReturnTerms("C01", 180, 30, Decimal(90), date(2024, 1, 1), "letter")])
NOW = datetime(2026, 3, 1, 5, tzinfo=UTC)


def item(item_id="I001"):
    return Item(
        id=item_id,
        company_id="C01",
        brand=f"Brand {item_id}",
        molecule="Paracetamol",
        strength="500 mg",
        unit="strip of 10 tablets",
        hsn="3004",
        gst_rate=Decimal("0.05"),
        mrp=Decimal("30.00"),
    )


ITEMS = {"I001": item()}


def reason(document="CN-77", kind=Reason.BREAKAGE, by="Ravi", note=""):
    return ReturnReason(document, kind, by, NOW, note)


def ledger_with_breakage(units=6, document="cn-77 ", where="RETURNS"):
    """Stock bought, sold, and some of it sent back broken on a credit note."""
    bought = replace(
        movement(MovementType.PURCHASE, 100, when=at(1), batch=KEY), rate=Decimal("20.00")
    )
    sold = movement(MovementType.SALE, -40, when=at(5), batch=KEY, party_id="CH1")
    returned = movement(
        MovementType.SALE_RETURN, units, when=at(20), batch=KEY, location_id=where, party_id="CH1"
    )
    return Ledger([bought, sold, replace(returned, document_ref=document)])


def stock_of(ledger):
    return StockData({}, ITEMS, {KEY: None}, ledger, (GODOWN, RETURNS), ON)


def test_only_returns_marked_as_breakage_count_and_only_what_is_still_there():
    ledger = ledger_with_breakage()
    places = (GODOWN, RETURNS)
    assert breakage_in_stock(ledger, [], places, on=ON) == Breakage({}, {})
    assert breakage_in_stock(ledger, [reason(kind=Reason.EXPIRY)], places, on=ON).units == 0
    breakage = breakage_in_stock(ledger, [reason()], places, on=ON)
    assert breakage.to_claim == {(KEY, "RETURNS"): 6}
    assert breakage.units == 6 and breakage.batches() == {KEY}

    # The credit note is matched however it is typed, and what has left is not counted again.
    gone = ledger_with_breakage()
    gone.append(movement(MovementType.WRITE_OFF, -4, when=at(25), batch=KEY, location_id="RETURNS"))
    assert breakage_in_stock(gone, [reason(" cn-77")], places, on=ON).to_claim == {
        (KEY, "RETURNS"): 2
    }
    assert normalise(" cn-77 ") == "CN-77"


def test_breakage_still_on_a_selling_shelf_is_kept_apart():
    breakage = breakage_in_stock(
        ledger_with_breakage(where="GODOWN"), [reason()], (GODOWN, RETURNS), on=ON
    )
    assert breakage.to_claim == {} and breakage.on_sale_shelves == {(KEY, "GODOWN"): 6}
    assert breakage.units == 0


def test_a_reversed_return_is_not_breakage():
    ledger = ledger_with_breakage()
    returned = next(m for m in ledger if m.kind is MovementType.SALE_RETURN)
    ledger.append(
        movement(
            MovementType.REVERSAL,
            -6,
            when=at(21),
            batch=KEY,
            location_id="RETURNS",
            reverses=returned.id,
        )
    )
    assert breakage_in_stock(ledger, [reason()], (GODOWN, RETURNS), on=ON).units == 0


def test_a_breakage_claim_is_valued_at_cost_times_what_the_company_credits():
    ledger = ledger_with_breakage()
    breakage = breakage_in_stock(ledger, [reason()], (GODOWN, RETURNS), on=ON)
    costs = batch_costs(ledger)
    claim = draft_breakage(breakage, costs, TERMS, ITEMS, company_id="C01", on=ON)
    assert claim.number == "BR/C01/260301"
    (line,) = claim.lines
    assert (line.units, line.rate, line.credit_percent) == (6, Decimal("20.00"), Decimal(90))
    assert claim.taxable_value == Decimal("108.00")  # 6 x 20 x 90%
    assert claim.total == Decimal("113.40")
    posting = claim_posting(claim, COMPANY, ITEMS, kind=KIND)
    letter = posting.files["BR-C01-260301.claim-letter.txt"]
    assert letter.startswith("BREAKAGE CLAIM")
    assert "that chemists sent back broken or damaged" in letter
    assert "under section 34 of the CGST Act" in letter
    assert posting.approval_id == "claim:BR/C01/260301"
    assert posting.summary.startswith("BR/C01/260301 on Aravalli Pharma: 6 units of 1 batch")


def test_nothing_is_claimed_without_a_cost_terms_or_units_left():
    ledger = ledger_with_breakage()
    breakage = breakage_in_stock(ledger, [reason()], (GODOWN, RETURNS), on=ON)
    costs = batch_costs(ledger)
    assert draft_breakage(breakage, {}, TERMS, ITEMS, company_id="C01", on=ON) is None
    assert draft_breakage(breakage, costs, TermsTable(), ITEMS, company_id="C01", on=ON) is None
    assert draft_breakage(breakage, costs, TERMS, ITEMS, company_id="C09", on=ON) is None
    taken = {(KEY, "RETURNS"): 6}
    assert draft_breakage(
        breakage, costs, TERMS, ITEMS, company_id="C01", on=ON, claimed=taken
    ) is None  # fmt: skip


def test_units_claimed_as_breakage_are_left_out_of_the_expiry_windows():
    ledger = ledger_with_breakage()
    stock = stock_of(ledger)
    breakage = breakage_in_stock(ledger, [reason()], stock.locations, on=ON)
    with_breakage = windows_for(stock, TERMS, [], breakage)
    without = windows_for(stock, TERMS, [])
    on_shelf = {w.batch: dict(w.by_location) for w in without}
    assert on_shelf[KEY]["RETURNS"] == 6
    assert KEY not in {w.batch for w in with_breakage}


def test_a_reason_is_recorded_once_and_a_different_one_is_refused(tmp_path):
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        assert store.save_return_reason(reason())
        assert not store.save_return_reason(reason(by="Asha"))
        with pytest.raises(RecordsError, match="CN-77 is already recorded as breakage, not expiry"):
            store.save_return_reason(reason(kind=Reason.EXPIRY))
        assert store.save_return_reason(reason("CN-78", Reason.EXPIRY, note="near expiry"))
    with RecordStore(path, create=False) as store:
        assert [(r.document_ref, str(r.reason), r.note) for r in store.return_reasons()] == [
            ("CN-77", "breakage", ""),
            ("CN-78", "expiry", "near expiry"),
        ]
    with (
        closing(sqlite3.connect(path)) as connection,
        pytest.raises(sqlite3.IntegrityError, match="only ever added to"),
    ):
        connection.execute("DELETE FROM return_reasons")


def test_a_reason_needs_a_credit_note_a_person_and_a_time_with_its_offset():
    with pytest.raises(ValueError, match="needs the credit note"):
        reason(document=" ")
    with pytest.raises(ValueError, match="needs who recorded it"):
        reason(by="")
    with pytest.raises(ValueError, match="timezone-aware"):
        ReturnReason("CN-77", Reason.BREAKAGE, "Ravi", datetime(2026, 3, 1, 10))


def test_a_database_from_the_tenth_schema_gains_the_return_reasons_table(tmp_path):
    path = tmp_path / "records.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.executescript(
            f"BEGIN; {''.join(MIGRATIONS[:10])} PRAGMA user_version = 10; COMMIT;"
        )
    with RecordStore(path) as store:
        assert store.return_reasons() == []
        store.save_return_reason(reason())
        assert store.return_reasons() == [reason()]
