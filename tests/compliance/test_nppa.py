from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from batchward.compliance.nppa import (
    NotificationError,
    NotifiedCeiling,
    convert,
    pack_contents,
    read_notification,
)
from batchward.compliance.prices import CeilingTable
from batchward.core.models import Item

HEADER = "Sl. No.,Name of the Scheduled Formulation,Dosage form & Strength,Unit,Ceiling Price (Rs.)"
EFFECTIVE = date(2026, 10, 1)

ATOR = Item(
    id="A10",
    company_id="C01",
    brand="Atopur 10",
    molecule="Atorvastatin",
    strength="10 mg",
    unit="strip of 10 tablets",
    hsn="3004",
    gst_rate=Decimal("0.05"),
    mrp=Decimal(70),
    dpco_scheduled=True,
)


def table(*rows):
    return read_notification([HEADER, *rows])


def notified(formulation, strength, unit, price, row=2):
    return NotifiedCeiling(row, formulation, strength, unit, Decimal(price))


def import_(rows, items):
    return convert(rows, items, reference="S.O. 1234(E)", effective_from=EFFECTIVE)


class TestReadNotification:
    def test_reads_each_row_of_the_table(self):
        (row,) = table("1,Atorvastatin,Tablet 10 mg,1 Tablet,6.42")
        assert row == NotifiedCeiling(
            2, "Atorvastatin", "Tablet 10 mg", "1 Tablet", Decimal("6.42")
        )
        assert row.strength == "10 mg"

    def test_header_spelling_and_order_do_not_matter_and_blank_lines_are_skipped(self):
        rows = read_notification(
            [
                "ceiling price (rs),UNIT,Name of the scheduled formulation,"
                "Dosage Form and Strength",
                '"₹1,234.50",1 Vial,Ceftriaxone,Injection 1 g',
                ",,,",
            ]
        )
        assert [(r.formulation, r.strength, r.unit, r.price) for r in rows] == [
            ("Ceftriaxone", "1 g", "1 Vial", Decimal("1234.50"))
        ]

    @pytest.mark.parametrize(
        ("lines", "message"),
        [
            ([], "empty"),
            (["Name of the Scheduled Formulation,Unit"], "no column"),
            ([HEADER, "1,Atorvastatin,Tablet 10 mg,,6.42"], "line 2 has no unit"),
            ([HEADER, "1,Atorvastatin,Tablet 10 mg,1 Tablet,six"], "line 2 has ceiling price"),
            ([HEADER, "1,Atorvastatin,Tablet 10 mg,1 Tablet,0"], "not a positive amount"),
        ],
    )
    def test_a_table_that_cannot_be_read_is_refused_naming_the_line(self, lines, message):
        with pytest.raises(NotificationError, match=message):
            read_notification(lines)


@pytest.mark.parametrize(
    ("unit", "contents"),
    [
        ("strip of 10 tablets", {"tablet": 10, "strip": 1}),
        ("bottle of 100 tablets", {"tablet": 100, "bottle": 1}),
        ("strip of 1 tablet", {"tablet": 1, "strip": 1}),
        ("10 ml vial", {"ml": 10, "vial": 1}),
        ("inhaler of 200 doses", {"dose": 200, "inhaler": 1}),
        ("vial", {"vial": 1}),
        ("prefilled pen", {"prefilled pen": 1, "pen": 1}),
    ],
)
def test_reads_what_one_unit_sold_holds(unit, contents):
    assert pack_contents(unit) == {key: Decimal(value) for key, value in contents.items()}


class TestConvert:
    def test_a_price_per_tablet_becomes_a_price_per_strip(self):
        result = import_([notified("Atorvastatin", "Tablet 10 mg", "1 Tablet", "6.42")], [ATOR])
        (price,) = result.prices
        assert (price.molecule, price.strength, price.unit) == (
            "Atorvastatin",
            "10 mg",
            "strip of 10 tablets",
        )
        assert (price.ceiling, price.effective_from, price.reference) == (
            Decimal("64.20"),
            EFFECTIVE,
            "S.O. 1234(E)",
        )
        assert CeilingTable(result.prices).in_force(ATOR, EFFECTIVE) == price

    def test_each_pack_of_a_formulation_gets_its_own_ceiling_and_brands_share_one(self):
        rival = replace(ATOR, id="A11", brand="Atogod 10", company_id="C02")
        bottle = replace(ATOR, id="A12", brand="Atopur 10 bottle", unit="bottle of 30 tablets")
        result = import_(
            [notified("Atorvastatin", "Tablet 10 mg", "1 Tablet", "6.42")], [ATOR, rival, bottle]
        )
        assert sorted((p.unit, p.ceiling) for p in result.prices) == [
            ("bottle of 30 tablets", Decimal("192.60")),
            ("strip of 10 tablets", Decimal("64.20")),
        ]

    def test_a_price_per_ml_is_multiplied_by_the_millilitres_in_the_vial(self):
        insulin = replace(
            ATOR, id="H40", molecule="Human insulin", strength="40 IU/ml", unit="10 ml vial"
        )
        (price,) = import_(
            [notified("Human Insulin", "Injection 40 IU/ml", "1 ml", "16.50")], [insulin]
        ).prices
        assert price.ceiling == Decimal("165.00")

    @pytest.mark.parametrize("unit", ["1 Tablet", "Each Tablet", "per tablet", "Tablet"])
    def test_the_notified_unit_may_be_written_several_ways(self, unit):
        (price,) = import_([notified("Atorvastatin", "Tablet 10 mg", unit, "6.42")], [ATOR]).prices
        assert price.ceiling == Decimal("64.20")

    def test_a_price_for_several_doses_is_divided_down_first(self):
        (price,) = import_(
            [notified("Atorvastatin", "Tablet 10 mg", "10 Tablets", "64.25")], [ATOR]
        ).prices
        assert price.ceiling == Decimal("64.25")

    def test_a_pack_that_does_not_say_how_much_it_holds_is_left_for_a_person(self):
        pen = replace(
            ATOR, id="G100", molecule="Insulin glargine", strength="100 IU/ml", unit="prefilled pen"
        )
        result = import_([notified("Insulin glargine", "Injection 100 IU/ml", "1 ml", "86")], [pen])
        assert result.prices == ()
        (gap,) = result.unconverted
        assert gap.item == pen
        assert "does not say how many of '1 ml' it holds" in gap.reason

    def test_a_notified_formulation_nobody_stocks_is_counted_not_guessed(self):
        row = notified("Ibuprofen", "Tablet 400 mg", "1 Tablet", "1.07")
        result = import_([row], [ATOR])
        assert (result.prices, result.not_stocked) == ((), (row,))

    def test_another_strength_is_another_formulation(self):
        result = import_([notified("Atorvastatin", "Tablet 20 mg", "1 Tablet", "12.10")], [ATOR])
        assert result.prices == () and len(result.not_stocked) == 1

    def test_an_item_not_marked_as_scheduled_is_reported_so_its_price_gets_checked(self):
        unmarked = replace(ATOR, dpco_scheduled=False)
        result = import_([notified("Atorvastatin", "Tablet 10 mg", "1 Tablet", "6.42")], [unmarked])
        assert result.not_marked_scheduled == (unmarked,)
        assert len(result.prices) == 1

    def test_two_lines_pricing_the_same_pack_differently_are_refused(self):
        rows = [
            notified("Atorvastatin", "Tablet 10 mg", "1 Tablet", "6.42", row=2),
            notified("Atorvastatin", "Tablet 10mg", "1 Tablet", "6.50", row=3),
        ]
        with pytest.raises(NotificationError, match="line 3 prices Atorvastatin 10 mg"):
            import_(rows, [ATOR])
