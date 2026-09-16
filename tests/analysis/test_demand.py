from datetime import UTC, date, time

import pytest

from batchward.analysis.demand import (
    DemandPattern,
    aggregate,
    classify_demand,
    daily_sales,
)
from batchward.core.clock import ist_datetime
from batchward.core.ledger import Ledger
from batchward.core.models import MovementType
from factories import at, batch_key, movement

PURCHASE = MovementType.PURCHASE
SALE = MovementType.SALE
SALE_RETURN = MovementType.SALE_RETURN


class TestDailySales:
    def test_counts_units_sold_per_item_per_day(self):
        ledger = Ledger(
            [
                movement(PURCHASE, 100, when=at(1)),
                movement(SALE, -4, when=at(2, 10)),
                movement(SALE, -6, when=at(2, 15)),
                movement(SALE, -3, when=at(4)),
            ]
        )
        series = daily_sales(ledger, start=date(2026, 1, 1), end=date(2026, 1, 5))
        assert series == {"I001": [0, 10, 0, 3, 0]}

    def test_does_not_net_off_returns(self):
        ledger = Ledger(
            [
                movement(PURCHASE, 100, when=at(1)),
                movement(SALE, -8, when=at(2), party_id="CH1"),
                movement(SALE_RETURN, 5, when=at(3), party_id="CH1"),
            ]
        )
        assert daily_sales(ledger, start=date(2026, 1, 2), end=date(2026, 1, 3)) == {"I001": [8, 0]}

    def test_leaves_out_a_sale_that_was_reversed(self):
        mistake = movement(SALE, -8, when=at(2))
        ledger = Ledger([movement(PURCHASE, 100, when=at(1)), mistake])
        ledger.reverse(mistake.id, reversal_id="R1", at=at(3), document_ref="CORR-1")
        assert daily_sales(ledger, start=date(2026, 1, 1), end=date(2026, 1, 3)) == {}

    def test_counts_a_sale_on_the_indian_calendar_day_not_the_utc_one(self):
        # 00:30 IST on 3 January is still 2 January in UTC.
        just_after_midnight = ist_datetime(date(2026, 1, 3), time(0, 30)).astimezone(UTC)
        assert just_after_midnight.date() == date(2026, 1, 2)
        ledger = Ledger(
            [
                movement(PURCHASE, 10, when=at(1)),
                movement(SALE, -2, when=just_after_midnight),
            ]
        )
        assert daily_sales(ledger, start=date(2026, 1, 2), end=date(2026, 1, 3)) == {"I001": [0, 2]}

    def test_ignores_sales_outside_the_window_and_items_without_sales(self):
        other = batch_key("X1", item_id="I002")
        ledger = Ledger(
            [
                movement(PURCHASE, 100, when=at(1)),
                movement(PURCHASE, 100, when=at(1), batch=other),
                movement(SALE, -5, when=at(9)),
            ]
        )
        assert daily_sales(ledger, start=date(2026, 1, 1), end=date(2026, 1, 3)) == {}

    def test_rejects_an_end_before_the_start(self):
        with pytest.raises(ValueError, match="before start"):
            daily_sales(Ledger(), start=date(2026, 1, 3), end=date(2026, 1, 2))


class TestAggregate:
    def test_sums_whole_periods(self):
        assert aggregate([1, 2, 3, 4, 5, 6], 3) == [6, 15]

    def test_drops_a_trailing_partial_period(self):
        assert aggregate([1, 1, 1, 1, 1, 1, 1, 9], 7) == [7]

    def test_rejects_a_non_positive_period(self):
        with pytest.raises(ValueError, match="positive"):
            aggregate([1, 2], 0)


class TestClassifyDemand:
    def test_steady_every_period_is_smooth(self):
        profile = classify_demand([10, 11, 9, 10, 12, 10, 9, 11])
        assert profile.pattern is DemandPattern.SMOOTH
        assert profile.adi == 1.0

    def test_every_period_but_wildly_varying_is_erratic(self):
        assert classify_demand([1, 40, 2, 35, 1, 50, 3, 45]).pattern is DemandPattern.ERRATIC

    def test_rare_but_even_is_intermittent(self):
        assert classify_demand([0, 0, 5, 0, 0, 6, 0, 0, 5, 0]).pattern is DemandPattern.INTERMITTENT

    def test_rare_and_uneven_is_lumpy(self):
        assert classify_demand([0, 0, 1, 0, 0, 40, 0, 0, 2, 0]).pattern is DemandPattern.LUMPY

    def test_no_sales_at_all(self):
        profile = classify_demand([0, 0, 0])
        assert profile.pattern is DemandPattern.NO_DEMAND
        assert (profile.adi, profile.cv2) == (None, None)

    def test_a_single_sale_has_no_size_variation(self):
        profile = classify_demand([0, 0, 0, 7])
        assert profile.cv2 == 0.0
        assert profile.pattern is DemandPattern.INTERMITTENT

    def test_reports_the_evidence_behind_the_pattern(self):
        profile = classify_demand([0, 4, 0, 4])
        assert (profile.periods, profile.periods_with_demand, profile.adi) == (4, 2, 2.0)

    def test_rejects_negative_demand(self):
        with pytest.raises(ValueError, match="negative"):
            classify_demand([3, -1])
