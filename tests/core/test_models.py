from datetime import date, datetime
from decimal import Decimal

import pytest

from batchward.core.models import (
    Batch,
    BatchStatus,
    Item,
    MovementType,
    Party,
    PartyKind,
)
from factories import at, batch_key, movement


class TestBatchKey:
    def test_normalises_whitespace_and_case(self):
        assert batch_key(" az 4021 ") == batch_key("AZ4021")

    def test_keeps_look_alike_characters_distinct(self):
        assert batch_key("J4021") != batch_key("J4O21")

    def test_same_batch_number_from_another_company_is_a_different_batch(self):
        assert batch_key("AZ4021", company_id="C01") != batch_key("AZ4021", company_id="C02")

    def test_same_batch_number_with_another_expiry_is_a_different_batch(self):
        assert batch_key(expiry=date(2027, 10, 31)) != batch_key(expiry=date(2028, 10, 31))

    def test_rejects_blank_batch_number(self):
        with pytest.raises(ValueError, match="blank"):
            batch_key("   ")


class TestBatch:
    def test_must_be_manufactured_before_expiry(self):
        with pytest.raises(ValueError, match="before it expires"):
            Batch(
                key=batch_key(expiry=date(2026, 1, 1)),
                manufactured=date(2026, 1, 1),
                mrp=Decimal(10),
            )

    def test_live_batch_is_sellable_before_expiry(self):
        batch = Batch(
            key=batch_key(expiry=date(2027, 1, 31)), manufactured=date(2025, 2, 1), mrp=Decimal(10)
        )
        assert batch.is_sellable(date(2027, 1, 30))

    def test_is_not_sellable_on_its_expiry_date(self):
        batch = Batch(
            key=batch_key(expiry=date(2027, 1, 31)), manufactured=date(2025, 2, 1), mrp=Decimal(10)
        )
        assert not batch.is_sellable(date(2027, 1, 31))

    @pytest.mark.parametrize(
        "status", [BatchStatus.BLOCKED, BatchStatus.RECALLED, BatchStatus.QUARANTINED]
    )
    def test_is_not_sellable_unless_live(self, status):
        batch = Batch(
            key=batch_key(), manufactured=date(2025, 2, 1), mrp=Decimal(10), status=status
        )
        assert not batch.is_sellable(date(2026, 1, 1))


class TestItem:
    def item(self, **overrides):
        fields = {
            "id": "I001",
            "company_id": "C01",
            "brand": "Azinil 500",
            "molecule": "Azithromycin",
            "strength": "500 mg",
            "unit": "strip of 3 tablets",
            "hsn": "3004",
            "gst_rate": Decimal("0.05"),
            "mrp": Decimal("71.50"),
        }
        return Item(**(fields | overrides))

    def test_accepts_a_fractional_gst_rate(self):
        assert self.item().gst_rate == Decimal("0.05")

    def test_rejects_a_percentage_written_as_a_whole_number(self):
        with pytest.raises(ValueError, match="fraction"):
            self.item(gst_rate=Decimal(5))

    def test_rejects_non_positive_mrp(self):
        with pytest.raises(ValueError, match="mrp"):
            self.item(mrp=Decimal(0))


class TestParty:
    def test_chemist_needs_a_drug_licence_number(self):
        with pytest.raises(ValueError, match="licence"):
            Party(id="P1", kind=PartyKind.CHEMIST, name="Agarwal Medical")

    def test_company_does_not_need_a_drug_licence_number(self):
        assert (
            Party(id="C01", kind=PartyKind.COMPANY, name="Nilgiri Pharma").drug_licence_no is None
        )


class TestStockMovement:
    def test_rejects_a_naive_timestamp(self):
        with pytest.raises(ValueError, match="timezone"):
            movement(MovementType.PURCHASE, 10, when=datetime(2026, 1, 1, 10))

    def test_rejects_zero_quantity(self):
        with pytest.raises(ValueError, match="zero"):
            movement(MovementType.ADJUSTMENT, 0)

    @pytest.mark.parametrize("kind", [MovementType.PURCHASE, MovementType.SALE_RETURN])
    def test_inbound_kinds_must_add_stock(self, kind):
        with pytest.raises(ValueError, match="add stock"):
            movement(kind, -5)

    @pytest.mark.parametrize("kind", [MovementType.SALE, MovementType.WRITE_OFF])
    def test_outbound_kinds_must_remove_stock(self, kind):
        with pytest.raises(ValueError, match="remove stock"):
            movement(kind, 5)

    @pytest.mark.parametrize("qty", [-3, 3])
    def test_adjustment_may_go_either_way(self, qty):
        assert movement(MovementType.ADJUSTMENT, qty).qty == qty

    def test_reversal_must_name_the_movement_it_reverses(self):
        with pytest.raises(ValueError, match="reverses"):
            movement(MovementType.REVERSAL, 5)

    def test_only_a_reversal_may_name_a_movement_to_reverse(self):
        with pytest.raises(ValueError, match="reverses"):
            movement(MovementType.PURCHASE, 5, reverses="M1")

    def test_timestamps_keep_their_timezone(self):
        assert movement(MovementType.PURCHASE, 5, when=at(2)).at.utcoffset().seconds == 19800
