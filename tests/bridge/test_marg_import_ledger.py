from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from batchward.bridge.marg_export import export_to_marg
from batchward.bridge.marg_import import MargDataError, read_ledger, read_masters
from batchward.core.ledger import Ledger
from batchward.core.models import Batch, Item, MovementType, Party, PartyKind
from factories import at, batch_key, movement


def rebuild(connection):
    return read_ledger(connection, read_masters(connection))


@pytest.fixture(scope="module")
def rebuilt(marg_template):
    """The whole ledger replayed from Marg once; tests must only read it."""
    return rebuild(marg_template)


def test_every_movement_comes_back(rebuilt, business):
    assert len(rebuilt) == len(business.ledger)


def test_balances_match_the_original_ledger(rebuilt, business):
    assert rebuilt.balances() == business.ledger.balances()


def test_movement_kinds_match_the_original_ledger(rebuilt, business):
    assert Counter(m.kind for m in rebuilt) == Counter(m.kind for m in business.ledger)


def test_movements_are_named_after_their_bill_and_line(rebuilt):
    first = next(iter(rebuilt))
    assert first.id == f"MARG:{first.document_ref}:1"


def test_rejects_an_unknown_voucher_type(exported):
    exported.execute("UPDATE \"DIS\" SET VTYPE = 'XX' WHERE VNO = 'OPENING' AND LINE = 3")
    with pytest.raises(MargDataError, match="bill OPENING line 3 has unknown voucher type 'XX'"):
        rebuild(exported)


def test_rejects_a_non_positive_quantity(exported):
    exported.execute("UPDATE \"DIS\" SET QTY = -4 WHERE VNO = 'OPENING' AND LINE = 3")
    with pytest.raises(MargDataError, match="quantity -4"):
        rebuild(exported)


def test_rejects_a_line_for_a_batch_with_no_record(exported):
    exported.execute("UPDATE \"DIS\" SET BATCH = 'GHOST' WHERE VNO = 'OPENING' AND LINE = 3")
    with pytest.raises(MargDataError, match="batch GHOST"):
        rebuild(exported)


def test_rejects_a_line_for_an_unknown_party(exported):
    exported.execute("UPDATE \"DIS\" SET PARTY = 'CH999' WHERE VTYPE = 'S' AND LINE = 1")
    with pytest.raises(MargDataError, match="unknown party 'CH999'"):
        rebuild(exported)


def test_a_purchase_and_sale_at_the_same_instant_replay_purchase_first(connection):
    """Marg bills are often dated but not timed; a same-day sale must not be refused."""
    company = Party(id="C01", kind=PartyKind.COMPANY, name="Nilgiri Labs")
    chemist = Party(
        id="CH001", kind=PartyKind.CHEMIST, name="Agarwal Medical", drug_licence_no="L1"
    )
    item = Item(
        id="I001",
        company_id="C01",
        brand="Azinil 500",
        molecule="Azithromycin",
        strength="500 mg",
        unit="strip of 3 tablets",
        hsn="3004",
        gst_rate=Decimal("0.05"),
        mrp=Decimal("72.00"),
    )
    key = batch_key(expiry=date(2027, 10, 31))
    noon = at(5, 12)
    sale = movement(MovementType.SALE, -8, when=noon, party_id="CH001")
    purchase = movement(MovementType.PURCHASE, 10, when=noon)
    ledger = Ledger([purchase, sale])
    export_to_marg(
        connection,
        parties=[company, chemist],
        items=[item],
        batches={key: Batch(key=key, manufactured=date(2025, 11, 1), mrp=Decimal("72.00"))},
        ledger=ledger,
    )
    # Put the sale's row first in the table, as an unordered Marg query might return it.
    connection.execute("UPDATE \"DIS\" SET VNO = 'A-SALE' WHERE VTYPE = 'S'")
    connection.execute("UPDATE \"DIS\" SET VNO = 'Z-PURCHASE' WHERE VTYPE = 'P'")

    rebuilt = rebuild(connection)
    assert [m.kind for m in rebuilt] == [MovementType.PURCHASE, MovementType.SALE]
    assert rebuilt.balance(key, "GODOWN") == 2
