"""Matching a notified formulation to a stocked one: only on a person's word, with help."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from batchward.compliance.equivalents import (
    CLOSE_ENOUGH,
    Candidate,
    EquivalenceError,
    Equivalent,
    EquivalentTable,
    apart,
    same_strength,
    strength_in_base,
    suggestions,
    words,
)
from batchward.core.clock import IST
from batchward.core.models import Item

AMOX = Item(
    id="A625",
    company_id="C01",
    brand="Moxiclav 625",
    molecule="Amoxicillin + Clavulanic acid",
    strength="625 mg",
    unit="strip of 10 tablets",
    hsn="3004",
    gst_rate=Decimal("0.12"),
    mrp=Decimal(180),
    dpco_scheduled=True,
)
NOTED = datetime(2026, 9, 28, 11, 0, tzinfo=IST)


def equivalent(**rest) -> Equivalent:
    fields = {
        "formulation": "Amoxycillin and Potassium Clavulanate",
        "strength": "500 mg + 125 mg",
        "molecule": "Amoxicillin + Clavulanic acid",
        "item_strength": "625 mg",
        "noted_by": "Divyansh",
        "noted_at": NOTED,
    } | rest
    return Equivalent(**fields)


class TestStrengths:
    @pytest.mark.parametrize(
        ("written", "expected"),
        [
            ("625 mg", (Decimal(625), "mg")),
            ("500 mg + 125 mg", (Decimal(625), "mg")),
            ("1 g", (Decimal(1000), "mg")),
            ("0.5 g", (Decimal("500.0"), "mg")),
            ("5 ml", (Decimal(5), "ml")),
            ("100 IU/ml", None),
            ("as directed", None),
            ("", None),
            ("500 mg + 5 ml", None),
        ],
    )
    def test_a_strength_is_reduced_to_one_amount_or_left_alone(self, written, expected):
        assert strength_in_base(written) == expected

    @pytest.mark.parametrize(
        ("one", "other"),
        [("625 mg", "625mg"), ("500 mg + 125 mg", "625 mg"), ("1 g", "1000 mg")],
    )
    def test_two_strengths_are_the_same_amount_however_they_are_written(self, one, other):
        assert same_strength(one, other)

    @pytest.mark.parametrize(("one", "other"), [("10 mg", "20 mg"), ("100 IU/ml", "100 mg")])
    def test_strengths_that_are_not_the_same_amount(self, one, other):
        assert not same_strength(one, other)


class TestNames:
    def test_how_far_apart_two_spellings_are(self):
        assert apart("Amoxycillin", "Amoxicillin") == 1
        assert apart("Paracetamol", "paracetamol") == 0
        assert apart("Paracetamol", "Atorvastatin") > CLOSE_ENOUGH

    def test_the_words_that_say_which_medicine_it_is(self):
        assert words("Amoxycillin and Potassium Clavulanate IP") == {
            "amoxycillin",
            "potassium",
            "clavulanate",
        }
        assert words("") == frozenset()


class TestSuggestions:
    def test_a_combination_notified_by_its_parts_is_offered_with_the_reason(self):
        (candidate,) = suggestions("Amoxicillin and Clavulanic acid", "500 mg + 125 mg", [AMOX])
        assert (candidate.molecule, candidate.strength) == (AMOX.molecule, "625 mg")
        assert candidate.items == 1
        assert candidate.reason == (
            "its parts add up to 625 mg; the names share amoxicillin and clavulanic"
        )

    def test_a_strength_written_in_another_unit_is_offered(self):
        gram = replace(AMOX, id="A1G", molecule="Metformin", strength="1 g")
        (candidate,) = suggestions("Metformin", "1000 mg", [gram])
        assert candidate.reason == "the same strength written as 1 g; the same name"

    def test_a_name_spelled_a_little_differently_is_offered(self):
        (candidate,) = suggestions("Amoxycillin + Clavulanic acid", "1 g", [AMOX])
        assert candidate.reason == "spelled 1 letter apart"

    def test_names_sharing_their_words_are_offered_with_the_words(self):
        (candidate,) = suggestions("Clavulanate with Amoxicillin", "875 mg", [AMOX])
        assert candidate.reason == "the names share amoxicillin"

    def test_a_formulation_nothing_resembles_is_not_guessed_at(self):
        assert suggestions("Insulin glargine", "100 IU/ml", [AMOX]) == ()

    def test_the_closest_come_first_and_no_more_than_asked_for(self):
        others = [
            replace(AMOX, id="A1", molecule="Amoxicillin", strength="500 mg"),
            replace(AMOX, id="A2", molecule="Amoxycillin + Clavulanic acid", strength="625 mg"),
        ]
        found = suggestions("Amoxicillin + Clavulanic acid", "500 mg + 125 mg", [AMOX, *others])
        assert [candidate.strength for candidate in found][:2] == ["625 mg", "625 mg"]
        assert len(suggestions("Amoxicillin", "500 mg", [AMOX, *others], most=1)) == 1

    def test_items_of_the_same_formulation_are_counted_together(self):
        twin = replace(AMOX, id="A626", brand="Clavam 625")
        (candidate,) = suggestions("Amoxycillin + Clavulanic acid", "625 mg", [AMOX, twin])
        assert candidate == Candidate(
            AMOX.molecule,
            "625 mg",
            2,
            "the same strength; spelled 1 letter apart",
            candidate.closeness,
        )


class TestRecordingOne:
    def test_what_it_reads_as(self):
        assert str(equivalent()) == (
            "Amoxycillin and Potassium Clavulanate 500 mg + 125 mg is "
            "Amoxicillin + Clavulanic acid 625 mg (Divyansh, 28/09/2026)"
        )

    @pytest.mark.parametrize(
        ("missing", "says"),
        [
            ("formulation", "the notified formulation"),
            ("strength", "the notified strength"),
            ("molecule", "the molecule"),
            ("item_strength", "the item's strength"),
            ("noted_by", "who recorded it"),
        ],
    )
    def test_an_equivalence_missing_a_part_is_refused(self, missing, says):
        with pytest.raises(EquivalenceError, match=says):
            equivalent(**{missing: "  "})

    def test_recording_a_name_as_itself_is_refused(self):
        with pytest.raises(EquivalenceError, match="already matches the item master"):
            equivalent(formulation="Amoxicillin", molecule="amoxicillin", strength="625 mg")

    def test_it_is_looked_up_however_the_name_is_typed(self):
        table = EquivalentTable([equivalent()])
        assert table.find("AMOXYCILLIN AND POTASSIUM CLAVULANATE", "500mg+125mg") is not None
        assert table.find("Amoxycillin and Potassium Clavulanate", "625 mg") is None
        assert table.find("Paracetamol", "500 mg") is None

    def test_the_latest_record_for_a_name_is_the_one_in_force(self):
        later = equivalent(molecule="Amoxicillin", noted_at=NOTED.replace(day=29), note="corrected")
        table = EquivalentTable([equivalent(), later])
        assert len(table) == 1
        found = table.find("Amoxycillin and Potassium Clavulanate", "500 mg + 125 mg")
        assert found is not None and found.molecule == "Amoxicillin"
        assert table.all() == (later,)

    def test_an_empty_table_finds_nothing(self):
        assert EquivalentTable().find("Anything", "1 mg") is None
