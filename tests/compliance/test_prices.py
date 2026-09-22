from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from batchward.compliance.prices import (
    Cause,
    CeilingPrice,
    CeilingTable,
    Verdict,
    check_batch_price,
    overcharge_exposure,
    price_rises,
)
from batchward.core.ledger import Ledger
from batchward.core.models import Batch, Item, MovementType
from factories import at, batch_key, movement

ATOR = Item(
    id="I001",
    company_id="C01",
    brand="Atopur 10",
    molecule="Atorvastatin",
    strength="10 mg",
    unit="strip of 10 tablets",
    hsn="3004",
    gst_rate=Decimal("0.05"),
    mrp=Decimal("73.76"),
    dpco_scheduled=True,
)
CEFIX = replace(ATOR, id="I002", brand="Cefpur 200", molecule="Cefixime", dpco_scheduled=False)
ITEMS = {item.id: item for item in (ATOR, CEFIX)}

FIRST = CeilingPrice(
    "Atorvastatin", "10 mg", "strip of 10 tablets", Decimal("70.25"), date(2023, 4, 1), "N/2023/1"
)
LOWERED = replace(
    FIRST, ceiling=Decimal("64.21"), effective_from=date(2026, 4, 1), reference="N/2026/17"
)
TABLE = CeilingTable([LOWERED, FIRST])

ATOR_BATCH = Batch(key=batch_key("AT7776"), manufactured=date(2025, 6, 1), mrp=Decimal("73.76"))


def cefixime(batch_no, made, mrp):
    key = batch_key(batch_no, item_id="I002", expiry=date(2028, 6, 30))
    return Batch(key=key, manufactured=made, mrp=Decimal(mrp))


class TestCeilingTable:
    def test_each_ceiling_is_in_force_from_its_date_until_the_next(self):
        assert TABLE.in_force(ATOR, date(2023, 3, 31)) is None
        assert TABLE.in_force(ATOR, date(2023, 4, 1)) == FIRST
        assert TABLE.in_force(ATOR, date(2026, 3, 31)) == FIRST
        assert TABLE.in_force(ATOR, date(2026, 4, 1)) == LOWERED
        assert len(TABLE) == 2

    def test_formulations_match_ignoring_case_and_spacing_only(self):
        loose = replace(ATOR, molecule="ATORVASTATIN", unit="strip of  10 tablets")
        assert TABLE.in_force(loose, date(2026, 5, 1)) == LOWERED
        assert TABLE.in_force(replace(ATOR, strength="20 mg"), date(2026, 5, 1)) is None

    @pytest.mark.parametrize(
        ("strength", "unit"), [("10MG", "strip of 10 tablets"), ("10 mg", "stripof10tablets")]
    )
    def test_formulations_match_however_they_are_spaced(self, strength, unit):
        assert TABLE.in_force(replace(ATOR, strength=strength, unit=unit), date(2026, 5, 1)) == (
            LOWERED
        )

    def test_two_ceilings_for_one_formulation_on_one_day_are_refused(self):
        with pytest.raises(ValueError, match="two ceiling prices"):
            CeilingTable([FIRST, replace(FIRST, ceiling=Decimal(60), reference="other")])

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (dict(ceiling=Decimal(0)), "must be between"),
            (dict(ceiling=Decimal("NaN")), "must be between"),
            (dict(ceiling=Decimal("Infinity")), "must be between"),
            (dict(ceiling=Decimal("0.001")), "must be between"),
            (dict(ceiling=Decimal("1E+26")), "must be between"),
            (dict(molecule=" "), "must name the molecule"),
            (dict(unit=""), "must name the molecule"),
            (dict(reference=" "), "must name the notification"),
        ],
    )
    def test_a_ceiling_needs_a_positive_price_and_its_source(self, change, message):
        with pytest.raises(ValueError, match=message):
            replace(FIRST, **change)

    def test_the_maximum_retail_price_is_the_ceiling_plus_gst_to_the_paisa(self):
        assert LOWERED.max_retail_price(Decimal("0.05")) == Decimal("67.42")


class TestCheckBeforeBilling:
    def test_blocks_a_printed_mrp_above_the_ceiling_in_force_and_names_the_source(self):
        check = check_batch_price(ATOR, ATOR_BATCH, on=date(2026, 4, 1), ceilings=TABLE)
        assert check.verdict is Verdict.BLOCK
        assert (check.max_retail_price, check.excess_per_unit) == (
            Decimal("67.42"),
            Decimal("6.34"),
        )
        assert "N/2026/17" in check.reason

    def test_the_same_batch_was_allowed_before_the_ceiling_was_lowered(self):
        check = check_batch_price(ATOR, ATOR_BATCH, on=date(2026, 3, 31), ceilings=TABLE)
        assert (check.verdict, check.excess_per_unit) == (Verdict.ALLOWED, Decimal(0))

    def test_an_mrp_exactly_at_the_limit_is_allowed(self):
        at_limit = replace(ATOR_BATCH, mrp=Decimal("67.42"))
        check = check_batch_price(ATOR, at_limit, on=date(2026, 5, 1), ceilings=TABLE)
        assert check.verdict is Verdict.ALLOWED

    def test_a_scheduled_formulation_with_no_ceiling_on_record_is_warned_not_allowed(self):
        check = check_batch_price(ATOR, ATOR_BATCH, on=date(2026, 5, 1), ceilings=CeilingTable())
        assert check.verdict is Verdict.WARN
        assert "no ceiling price on record" in check.reason

    def test_a_non_scheduled_formulation_has_no_ceiling_to_break(self):
        batch = cefixime("CF1001", date(2025, 6, 1), "500")
        check = check_batch_price(CEFIX, batch, on=date(2026, 5, 1), ceilings=CeilingTable())
        assert check.verdict is Verdict.ALLOWED

    def test_refuses_a_batch_of_another_item(self):
        with pytest.raises(ValueError, match="not a batch of item"):
            check_batch_price(CEFIX, ATOR_BATCH, on=date(2026, 5, 1), ceilings=TABLE)


class TestPriceRises:
    def test_flags_an_mrp_more_than_ten_percent_above_the_lowest_in_the_year_before(self):
        batches = [
            cefixime("CF1001", date(2025, 3, 1), "90.00"),
            cefixime("CF1002", date(2025, 8, 1), "80.00"),
            cefixime("CF2601", date(2026, 1, 15), "96.29"),
        ]
        (rise,) = price_rises(batches, ITEMS)
        assert (rise.batch.batch_no, rise.reference_batch.batch_no) == ("CF2601", "CF1002")
        assert (rise.allowed_mrp, rise.excess_per_unit) == (Decimal("88.00"), Decimal("8.29"))

    def test_a_rise_of_exactly_ten_percent_is_allowed(self):
        batches = [
            cefixime("CF1", date(2025, 8, 1), "80.00"),
            cefixime("CF2", date(2026, 1, 1), "88.00"),
        ]
        assert price_rises(batches, ITEMS) == []

    def test_a_rise_over_ten_percent_by_less_than_half_a_paisa_is_still_a_rise(self):
        batches = [
            cefixime("CF1", date(2025, 8, 1), "80.05"),
            cefixime("CF2", date(2026, 1, 1), "88.06"),
        ]
        (rise,) = price_rises(batches, ITEMS)
        assert (rise.limit, rise.allowed_mrp, rise.excess_per_unit) == (
            Decimal("88.0550"),
            Decimal("88.05"),
            Decimal("0.01"),
        )
        batches[1] = cefixime("CF2", date(2026, 1, 1), "88.05")
        assert price_rises(batches, ITEMS) == []

    def test_batches_made_more_than_a_year_before_do_not_count(self):
        batches = [
            cefixime("CF1", date(2024, 12, 1), "50.00"),
            cefixime("CF2", date(2026, 1, 1), "90.00"),
        ]
        assert price_rises(batches, ITEMS) == []

    def test_scheduled_formulations_are_judged_by_their_ceiling_instead(self):
        earlier = replace(
            ATOR_BATCH, key=batch_key("AT1"), manufactured=date(2025, 1, 1), mrp=Decimal(40)
        )
        assert price_rises([earlier, ATOR_BATCH], ITEMS) == []


def sold(batch, qty, day, month, year=2026):
    return movement(
        MovementType.SALE,
        -qty,
        batch=batch.key,
        when=at(day, month=month, year=year),
        party_id="CH1",
    )


class TestOverchargeExposure:
    def exposure(self, *moves, batches=(ATOR_BATCH,), as_of=date(2026, 6, 13)):
        purchases = [
            movement(MovementType.PURCHASE, 1_000, batch=b.key, when=at(1, year=2025))
            for b in batches
        ]
        ledger = Ledger([*purchases, *moves])
        return overcharge_exposure(
            ledger, {b.key: b for b in batches}, ITEMS, TABLE, as_of=as_of
        ), ledger

    def test_charges_every_unit_sold_above_the_ceiling_with_simple_interest_to_the_day(self):
        result, _ = self.exposure(sold(ATOR_BATCH, 10, 1, 4))
        (line,) = result.overcharges
        assert (line.cause, line.units, line.excess_per_unit) == (
            Cause.ABOVE_CEILING,
            10,
            Decimal("6.34"),
        )
        assert line.amount == Decimal("63.40")
        assert line.days == 73
        assert line.interest == Decimal("1.90")  # 63.40 x 15% x 73/365
        assert (result.amount, result.interest, result.total) == (
            Decimal("63.40"),
            Decimal("1.90"),
            Decimal("65.30"),
        )
        assert line.source == "N/2026/17"

    def test_sales_before_the_ceiling_took_effect_or_after_the_report_date_are_not_counted(self):
        result, _ = self.exposure(sold(ATOR_BATCH, 10, 31, 3), sold(ATOR_BATCH, 5, 14, 6))
        assert result.overcharges == ()

    def test_a_reversed_sale_is_not_an_overcharge(self):
        sale = sold(ATOR_BATCH, 10, 1, 4)
        result, ledger = self.exposure(sale)
        ledger.reverse(sale.id, reversal_id="R1", at=at(2, month=4), document_ref="CANCEL")
        again = overcharge_exposure(
            ledger, {ATOR_BATCH.key: ATOR_BATCH}, ITEMS, TABLE, as_of=date(2026, 6, 13)
        )
        assert len(result.overcharges) == 1
        assert again.overcharges == ()

    def test_sales_of_a_batch_whose_price_rose_too_far_are_counted_too(self):
        before = cefixime("CF1", date(2025, 8, 1), "80.00")
        after = cefixime("CF2", date(2026, 1, 15), "96.29")
        result, _ = self.exposure(
            sold(after, 4, 1, 3), sold(before, 9, 1, 3), batches=(before, after)
        )
        (line,) = result.overcharges
        assert (line.cause, line.amount) == (Cause.PRICE_RISE, Decimal("33.16"))
        assert line.source == "batch CF1 at MRP 80.00"

    def test_groups_by_batch_largest_overcharge_first(self):
        other = replace(ATOR_BATCH, key=batch_key("AT9999"))
        result, _ = self.exposure(
            sold(ATOR_BATCH, 2, 1, 4), sold(other, 30, 2, 4), sold(ATOR_BATCH, 3, 5, 4),
            batches=(ATOR_BATCH, other),
        )  # fmt: skip
        grouped = result.by_batch()
        assert list(grouped) == [other.key, ATOR_BATCH.key]
        assert grouped[ATOR_BATCH.key][:2] == (5, Decimal("31.70"))
        assert [o.units for o in result.overcharges] == [30, 3, 2]

    def test_interest_is_never_negative(self):
        result, _ = self.exposure(sold(ATOR_BATCH, 1, 1, 4), as_of=date(2026, 4, 1))
        assert result.overcharges[0].days == 0
        assert result.interest == Decimal(0)
