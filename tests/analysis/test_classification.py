from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from batchward.analysis.classification import (
    ValueClass,
    VariabilityClass,
    classify_abc,
    classify_xyz,
    consumption_value,
)
from batchward.core.ledger import Ledger
from batchward.core.models import MovementType
from factories import at, batch_key, movement

A, B, C = ValueClass.A, ValueClass.B, ValueClass.C


class TestConsumptionValue:
    def test_values_units_sold_in_the_window_at_batch_cost(self):
        cheap, dear = batch_key("CHEAP"), batch_key("DEAR", item_id="I002")
        ledger = Ledger(
            [
                movement(MovementType.PURCHASE, 100, when=at(1), batch=cheap),
                movement(MovementType.PURCHASE, 100, when=at(1), batch=dear),
                movement(MovementType.SALE, -10, when=at(2), batch=cheap),
                movement(MovementType.SALE, -4, when=at(3), batch=dear),
                movement(MovementType.SALE, -50, when=at(20), batch=cheap),
            ]
        )
        costs = {cheap: Decimal("5.00"), dear: Decimal("80.00")}
        values = consumption_value(ledger, costs, start=date(2026, 1, 1), end=date(2026, 1, 10))
        assert values == {"I001": Decimal("50.00"), "I002": Decimal("320.00")}

    def test_leaves_out_reversed_sales_and_batches_without_a_cost(self):
        known, unknown = batch_key("KNOWN"), batch_key("UNKNOWN", item_id="I002")
        wrong = movement(MovementType.SALE, -10, when=at(2), batch=known)
        ledger = Ledger(
            [
                movement(MovementType.PURCHASE, 100, when=at(1), batch=known),
                movement(MovementType.PURCHASE, 100, when=at(1), batch=unknown),
                wrong,
                movement(MovementType.SALE, -5, when=at(2), batch=unknown),
            ]
        )
        ledger.reverse(wrong.id, reversal_id="R1", at=at(3), document_ref="CORR-1")
        costs = {known: Decimal("5.00")}
        assert consumption_value(ledger, costs, start=date(2026, 1, 1), end=date(2026, 1, 9)) == {}

    def test_as_of_a_past_moment_a_sale_reversed_only_later_still_counts(self):
        sale = movement(MovementType.SALE, -10, when=at(2))
        ledger = Ledger([movement(MovementType.PURCHASE, 100, when=at(1)), sale])
        ledger.reverse(sale.id, reversal_id="R1", at=at(20), document_ref="CORR-1")
        window = dict(start=date(2026, 1, 1), end=date(2026, 1, 9))
        costs = {batch_key(): Decimal("5.00")}
        assert consumption_value(ledger, costs, **window, as_of=at(9)) == {"I001": Decimal("50.00")}
        assert consumption_value(ledger, costs, **window) == {}


class TestClassifyAbc:
    def test_the_items_carrying_the_first_80_percent_of_value_are_a(self):
        values = {
            "big": Decimal(500),
            "mid": Decimal(300),
            "small": Decimal(150),
            "tiny": Decimal(50),
        }
        # Share before each: big 0, mid 0.50, small 0.80, tiny 0.95.
        assert classify_abc(values) == {"big": A, "mid": A, "small": B, "tiny": C}

    def test_a_single_dominant_item_is_a(self):
        assert classify_abc({"only": Decimal(990), "rest": Decimal(10)}) == {"only": A, "rest": C}

    def test_items_with_no_value_are_c(self):
        assert classify_abc({"sold": Decimal(100), "none": Decimal(0)}) == {"sold": A, "none": C}

    def test_ties_break_by_item_id_so_results_are_stable(self):
        values = {"b": Decimal(50), "a": Decimal(50)}
        assert classify_abc(values, a_share=0.5, b_share=0.9) == {"a": A, "b": B}

    def test_rejects_boundaries_out_of_order(self):
        with pytest.raises(ValueError, match="shares"):
            classify_abc({"x": Decimal(1)}, a_share=0.9, b_share=0.8)


class TestClassifyXyz:
    def test_steady_weekly_demand_is_x(self):
        result = classify_xyz([20, 22, 19, 21, 20])
        assert result.variability_class is VariabilityClass.X
        # Population standard deviation 1.0198 over a mean of 20.4.
        assert result.cv == pytest.approx(0.0500, abs=1e-3)

    def test_variable_demand_is_y(self):
        assert classify_xyz([10, 30, 5, 25, 15]).variability_class is VariabilityClass.Y

    def test_erratic_or_rare_demand_is_z(self):
        assert classify_xyz([0, 0, 40, 0, 0, 0]).variability_class is VariabilityClass.Z

    def test_an_item_that_never_sold_is_z_with_no_cv(self):
        assert classify_xyz([0, 0, 0]) == classify_xyz([])
        assert classify_xyz([0, 0]).cv is None

    def test_rejects_limits_out_of_order(self):
        with pytest.raises(ValueError, match="limits"):
            classify_xyz([1, 2], x_limit=1.0, y_limit=0.5)


def test_sale_rates_do_not_affect_consumption_value_only_batch_cost_does():
    batch = batch_key()
    sale = replace(movement(MovementType.SALE, -2, when=at(2)), rate=Decimal("999.00"))
    ledger = Ledger([movement(MovementType.PURCHASE, 10, when=at(1)), sale])
    values = consumption_value(
        ledger, {batch: Decimal("7.50")}, start=date(2026, 1, 1), end=date(2026, 1, 3)
    )
    assert values == {"I001": Decimal("15.00")}
