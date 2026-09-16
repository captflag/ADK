from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from batchward.core.models import MovementType, PartyKind
from batchward.sim.business import (
    IST,
    RETURNS,
    SimConfig,
    location_for,
    price_to_retailer,
    price_to_stockist,
    seasonal_factor,
    simulate,
)
from batchward.sim.catalogue import Therapy, build_catalogue

START = date(2026, 1, 1)


def small_business(days=45, seed=3, **overrides):
    catalogue = build_catalogue(seed=seed, n_companies=4, brands_per_company=(3, 5))
    config = SimConfig(start=START, days=days, seed=seed, n_chemists=25, **overrides)
    return simulate(config, catalogue)


@pytest.fixture(scope="module")
def business():
    return small_business(days=90)


def of_kind(business, kind):
    return [m for m in business.ledger if m.kind is kind]


def test_the_same_seed_replays_the_same_history():
    assert list(small_business(days=20).ledger) == list(small_business(days=20).ledger)


def test_never_sells_a_batch_on_or_after_its_expiry(business):
    sales = of_kind(business, MovementType.SALE)
    assert sales
    assert all(m.at.date() < m.batch.expiry for m in sales)


def test_every_sale_names_a_licensed_chemist_and_a_rate(business):
    chemists = {c.id: c for c in business.chemists}
    for m in of_kind(business, MovementType.SALE):
        assert chemists[m.party_id].kind is PartyKind.CHEMIST
        assert chemists[m.party_id].drug_licence_no
        assert m.rate is not None and m.rate > 0


def test_cold_chain_stock_lives_only_in_the_cold_room(business):
    items = {item.id: item for item in business.catalogue.items}
    for m in business.ledger:
        if m.location_id != RETURNS.id:
            assert m.location_id == location_for(items[m.batch.item_id])


def test_nothing_is_ever_sold_from_the_returns_shelf(business):
    assert all(m.location_id != RETURNS.id for m in of_kind(business, MovementType.SALE))


@pytest.fixture(scope="module")
def near_expiry_business():
    # Short remaining shelf life and generous cover leave stock on chemists' shelves.
    return small_business(days=150, opening_shelf_life_months=(3, 4), cover_days=60)


def test_chemists_return_near_expiry_stock_to_the_returns_shelf(near_expiry_business):
    returns = of_kind(near_expiry_business, MovementType.SALE_RETURN)
    assert returns
    for m in returns:
        assert m.location_id == RETURNS.id
        assert m.party_id is not None
        assert (m.batch.expiry - m.at.date()).days == 90


def test_returned_stock_is_written_off_when_it_expires(near_expiry_business):
    returned = {m.batch for m in of_kind(near_expiry_business, MovementType.SALE_RETURN)}
    written_off = {
        m.batch
        for m in of_kind(near_expiry_business, MovementType.WRITE_OFF)
        if m.location_id == RETURNS.id
    }
    expired_in_run = {key for key in returned if key.expiry < START + timedelta(days=150)}
    assert expired_in_run
    assert expired_in_run <= written_off


def test_every_batch_in_the_ledger_has_a_batch_record(business):
    assert {m.batch for m in business.ledger} <= set(business.batches)


def test_weekly_purchase_orders_replenish_stock(business):
    orders = [m for m in of_kind(business, MovementType.PURCHASE) if m.document_ref != "OPENING"]
    assert orders
    assert all(m.at.time() == time(9, 0) for m in orders)


def test_expired_stock_is_written_off_on_its_expiry_date():
    # Four months of cover that expires within two cannot all sell in time.
    business = small_business(days=100, opening_shelf_life_months=(1, 2), cover_days=120)
    write_offs = of_kind(business, MovementType.WRITE_OFF)
    assert write_offs
    for m in write_offs:
        assert m.at.date() == m.batch.expiry
        assert business.ledger.balance(m.batch, m.location_id) == 0


def test_stockouts_are_recorded_as_unmet_demand():
    business = small_business(days=40, cover_days=1, lead_time_days=(12, 14))
    assert sum(business.unmet_demand.values()) > 0


def test_sundays_sell_far_fewer_units_than_weekdays(business):
    days = Counter()
    units = Counter()
    for offset in range(90):
        day = date.fromordinal(START.toordinal() + offset)
        days[day.weekday() == 6] += 1
    for m in of_kind(business, MovementType.SALE):
        units[m.at.weekday() == 6] -= m.qty
    sunday_rate = units[True] / days[True]
    weekday_rate = units[False] / days[False]
    assert sunday_rate < weekday_rate / 2


def test_trading_happens_in_business_hours(business):
    for m in of_kind(business, MovementType.SALE):
        assert time(10, 0) <= m.at.astimezone(IST).time() <= time(19, 30)


def test_monsoon_lifts_anti_infective_demand_and_leaves_chronic_flat():
    assert seasonal_factor(Therapy.ANTI_INFECTIVE, 8) > 1
    assert seasonal_factor(Therapy.CHRONIC, 8) == 1


def test_trade_prices_follow_the_margin_working():
    # MRP 100 at 5% GST with 20% retailer and 10% stockist margins.
    assert price_to_retailer(Decimal(100), Decimal("0.05")) == Decimal("76.19")
    assert price_to_stockist(Decimal(100), Decimal("0.05")) == Decimal("68.57")


def test_rejects_more_chemists_than_the_name_pool_holds():
    with pytest.raises(ValueError, match="n_chemists"):
        simulate(SimConfig(start=START, days=1, n_chemists=5_000))


def test_all_timestamps_are_timezone_aware(business):
    assert all(isinstance(m.at, datetime) and m.at.tzinfo is not None for m in business.ledger)
