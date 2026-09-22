import csv
import io
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from batchward.claims.claim import (
    CLAIM_COLUMNS,
    RETURN_COLUMNS,
    Claim,
    ClaimLine,
    Settlement,
    claim_posting,
    draft_claim,
    settled,
)
from batchward.claims.submission import still_in_stock, submit
from batchward.claims.terms import ReturnTerms, TermsTable
from batchward.claims.windows import claim_windows
from batchward.core.approvals import Approval
from batchward.core.ledger import Ledger
from batchward.core.models import Item, Location, MovementType, Party, PartyKind
from batchward.records.store import MIGRATIONS, RecordsError, RecordStore
from factories import at, batch_key, movement

ON = date(2026, 3, 1)
COMPANY = Party("C01", PartyKind.COMPANY, "Aravalli Pharma", gstin="27AAPFU0939F1ZV")
ITEM = Item(
    id="I001",
    company_id="C01",
    brand="Azee 500",
    molecule="Azithromycin",
    strength="500 mg",
    unit="strip of 3 tablets",
    hsn="3004",
    gst_rate=Decimal("0.05"),
    mrp=Decimal("120.00"),
)
TERMS = ReturnTerms("C01", 90, 30, Decimal(90), date(2025, 1, 1), "letter")
OPEN_BATCH = batch_key("OPEN1", expiry=date(2026, 4, 30))
LATER_BATCH = batch_key("LATER", expiry=date(2026, 12, 31))


def line(batch=OPEN_BATCH, place="RETURNS", units=10, rate="20.00", bought=date(2026, 1, 1)):
    return ClaimLine(batch, place, units, Decimal(rate), Decimal(90), Decimal("0.05"), bought)


def claim(*lines):
    return Claim("CL/C01/260301", "C01", ON, lines or (line(),))


def drafted(**kwargs):
    ledger = Ledger(
        movement(MovementType.PURCHASE, units, when=at(1, month=1), batch=key, location_id=place)
        for key, units, place in (
            (OPEN_BATCH, 8, "RETURNS"),
            (OPEN_BATCH, 100, "GODOWN"),
            (LATER_BATCH, 5, "RETURNS"),
        )
    )
    windows = claim_windows(
        ledger,
        {OPEN_BATCH: Decimal("20.00"), LATER_BATCH: Decimal("20.00")},
        [Location("GODOWN", "Godown"), Location("RETURNS", "Returns", sellable=False)],
        {"I001": 1.0},
        TermsTable([TERMS]),
        on=ON,
        **kwargs,
    )
    return draft_claim(windows, {"I001": ITEM}, company_id="C01", on=ON)


def test_a_claim_takes_only_open_windows_from_where_the_units_are():
    made = drafted()
    assert made.number == "CL/C01/260301"
    assert [(ln.batch.batch_no, ln.location_id, ln.units) for ln in made.lines] == [
        ("OPEN1", "RETURNS", 8),
        ("OPEN1", "GODOWN", 40),
    ]
    assert made.taxable_value == Decimal("864.00")  # 48 x 20 x 90%
    assert made.tax_amount == Decimal("43.20")
    assert made.total == Decimal("907.20")
    assert draft_claim([], {}, company_id="C01", on=ON) is None


def test_the_claim_files_carry_the_same_lines_and_totals():
    made = claim(line(), line(place="GODOWN", units=5, bought=date(2025, 8, 1)))
    posting = claim_posting(made, COMPANY, {"I001": ITEM})
    assert posting.approval_id == "claim:CL/C01/260301"
    assert posting.summary.startswith("CL/C01/260301 on Aravalli Pharma: 15 units of 1 batch")
    sheet = list(csv.reader(io.StringIO(posting.files["CL-C01-260301.claim.csv"])))
    assert tuple(sheet[0]) == CLAIM_COLUMNS
    assert sum(Decimal(row[-1]) for row in sheet[1:]) == made.total
    voucher = list(csv.reader(io.StringIO(posting.files["CL-C01-260301.marg-purchase-return.csv"])))
    assert tuple(voucher[0]) == RETURN_COLUMNS
    assert [(row[8], row[9]) for row in voucher[1:]] == [("RETURNS", "10"), ("GODOWN", "5")]
    letter = posting.files["CL-C01-260301.claim-letter.txt"]
    assert "section 34" in letter and "Nothing has been sent" in letter
    assert "rate cut of 22/09/2025" in letter and "OPEN1" in letter
    assert posting.digest == claim_posting(made, COMPANY, {"I001": ITEM}).digest


def test_a_claim_is_for_one_company_and_something():
    with pytest.raises(ValueError, match="at least one line"):
        Claim("CL/C01/260301", "C01", ON, ())
    with pytest.raises(ValueError, match="another company's batch"):
        Claim("CL/C02/260301", "C02", ON, (line(),))


def test_units_claimed_stay_pending_until_marg_shows_them_returned():
    made = claim(line(units=10), line(place="GODOWN", units=4))
    ledger = Ledger(
        [movement(MovementType.PURCHASE, 20, when=at(1), batch=OPEN_BATCH, location_id="RETURNS")]
    )
    assert still_in_stock([made], ledger) == {
        (OPEN_BATCH, "RETURNS"): 10,
        (OPEN_BATCH, "GODOWN"): 4,
    }
    returned = replace(
        movement(MovementType.PURCHASE_RETURN, -6, when=at(2), batch=OPEN_BATCH),
        location_id="RETURNS",
        document_ref="CL/C01/260301",
    )
    ledger.append(returned)
    assert still_in_stock([made], ledger) == {(OPEN_BATCH, "RETURNS"): 4, (OPEN_BATCH, "GODOWN"): 4}


def test_what_is_still_owed_is_the_claim_less_its_credit_notes():
    notes = [
        Settlement("CL/C01/260301", "CN-1", Decimal("500.00"), date(2026, 4, 1), "Ravi"),
        Settlement("CL/C01/260301", "CN-2", Decimal("300.00"), date(2026, 4, 9), "Ravi"),
    ]
    assert settled(Decimal("907.20"), notes) == (Decimal("800.00"), Decimal("107.20"))
    assert settled(Decimal("700.00"), notes) == (Decimal("800.00"), Decimal(0))
    with pytest.raises(ValueError, match="more than nothing"):
        Settlement("CL/C01/260301", "CN-3", Decimal(0), ON, "Ravi")
    with pytest.raises(ValueError, match="credit note number"):
        Settlement("CL/C01/260301", " ", Decimal(1), ON, "Ravi")


def approved(store, made):
    posting = claim_posting(made, COMPANY, {"I001": ITEM})
    store.save_approval(
        Approval(
            posting.approval_id,
            "expiry claim",
            "Ravi",
            datetime(2026, 3, 1, 10, tzinfo=UTC),
            posting.digest,
            posting.summary,
        )
    )
    return posting.approval_id


def test_terms_claims_and_credit_notes_read_back_from_the_records(tmp_path):
    made = claim(line(), line(place="GODOWN", units=5, bought=None))
    with RecordStore(tmp_path / "records.sqlite") as store:
        assert store.save_terms(TERMS) is True
        assert store.save_terms(TERMS) is False
        with pytest.raises(RecordsError, match="different return terms for C01"):
            store.save_terms(replace(TERMS, credit_percent=Decimal(50)))
        assert store.terms_table().in_force("C01", ON) == TERMS

        store.save_claim(made, approved(store, made))
        with pytest.raises(RecordsError, match="cannot record claim"):
            store.save_claim(made, "claim:CL/C01/260301")
        assert store.claims() == [made]
        assert store.claim("cl/c01/260301") == made

        note = Settlement(made.number, "CN-1", Decimal("100.00"), date(2026, 4, 1), "Ravi")
        assert store.save_settlement(note) is True
        assert store.save_settlement(replace(note, credit_note="cn-1")) is False
        with pytest.raises(RecordsError, match="already recorded against"):
            store.save_settlement(replace(note, amount=Decimal("99.00")))
        with pytest.raises(RecordsError, match="no claim CL/C09/260301"):
            store.save_settlement(replace(note, claim="CL/C09/260301"))
        assert store.settlements(made.number) == [note]


def test_a_claim_needs_its_approval_and_claims_are_never_changed(tmp_path):
    path = tmp_path / "records.sqlite"
    made = claim()
    with RecordStore(path) as store:
        with pytest.raises(RecordsError, match="cannot record claim"):
            store.save_claim(made, "claim:not-approved")
        store.save_claim(made, approved(store, made))
        store.save_terms(TERMS)
    with closing(sqlite3.connect(path)) as connection:
        for table in ("return_terms", "claims", "claim_lines"):
            with pytest.raises(sqlite3.IntegrityError, match="only ever added to"):
                connection.execute(f"DELETE FROM {table}")


def test_a_database_from_the_fifth_schema_gains_the_claims_tables(tmp_path):
    path = tmp_path / "records.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.executescript(
            f"BEGIN; {''.join(MIGRATIONS[:5])} PRAGMA user_version = 5; COMMIT;"
        )
    with RecordStore(path) as store:
        store.save_terms(TERMS)
    with RecordStore(path, create=False) as store:
        assert store.return_terms() == [TERMS]
        assert store.claims() == []


def test_a_claim_changed_since_it_was_put_up_is_not_made(tmp_path):
    made = claim()
    planned = claim_posting(made, COMPANY, {"I001": ITEM})
    changed = claim_posting(claim(line(units=11)), COMPANY, {"I001": ITEM})
    at_ = datetime(2026, 3, 1, 11, tzinfo=UTC)
    with RecordStore(tmp_path / "records.sqlite") as store:
        with pytest.raises(RecordsError, match="has changed since it was put up"):
            submit(store, made, changed, planned, approved_by="Ravi", at=at_, out=tmp_path)
        first = submit(store, made, planned, planned, approved_by="Ravi", at=at_, out=tmp_path)
        assert len(first.written) == 3 and store.claims() == [made]
        again = submit(store, made, planned, planned, approved_by="Asha", at=at_, out=tmp_path)
        assert again.written == () and again.approval.approved_by == "Ravi"
