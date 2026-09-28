"""Choosing which cases to ask, and what each case needs the data to hold."""

from __future__ import annotations

from batchward.evals.cases import CASES, HOLDS, chosen, holds


class TestChoosingCases:
    def test_a_case_is_left_out_when_the_data_has_nothing_for_it(self, stock):
        there = holds()
        assert "the product" in there
        asked, left_out = chosen()
        assert {case.name for case in asked} <= {case.name for case in CASES}
        for case, why in left_out:
            assert why.startswith("the data has no ")
            assert case.needs

    def test_the_cases_asked_for_by_name_are_the_ones_run(self):
        asked, _ = chosen(["stock-health", "hindi"], has=())
        assert [case.name for case in asked] == ["stock-health", "hindi"]

    def test_every_case_needs_only_what_the_data_can_be_asked_for(self):
        for case in CASES:
            assert set(case.needs) <= set(HOLDS), case.name
