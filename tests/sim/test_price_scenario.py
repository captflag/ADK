from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from batchward.compliance.prices import Cause, Verdict, check_batch_price, overcharge_exposure
from batchward.core.clock import ist_date
from batchward.core.models import MovementType
from batchward.sim.business import SimConfig, simulate
from batchward.sim.price_scenario import REVISION_DAY, WPI_CHANGE_2026, seed_price_control

END = date(2026, 6, 1)


@pytest.fixture(scope="module")
def priced():
    business = simulate(SimConfig(start=date(2026, 1, 1), days=(END - date(2026, 1, 1)).days))
    scenario = seed_price_control(business)
    items = {item.id: item for item in business.catalogue.items}
    return business, scenario, items


def test_every_scheduled_formulation_has_a_ceiling_and_every_brand_complied_at_first(priced):
    business, scenario, items = priced
    for batch in business.batches.values():
        item = items[batch.key.item_id]
        check = check_batch_price(item, batch, on=date(2026, 3, 31), ceilings=scenario.ceilings)
        assert check.verdict is Verdict.ALLOWED, check.reason


def test_the_annual_revision_raises_the_other_ceilings_by_the_index(priced):
    _, scenario, items = priced
    pantoprazole = next(item for item in items.values() if item.molecule == "Pantoprazole")
    before = scenario.ceilings.in_force(pantoprazole, REVISION_DAY - timedelta(days=1))
    after = scenario.ceilings.in_force(pantoprazole, REVISION_DAY)
    assert after.reference == "SIM/NPPA/2026/WPI"
    assert after.ceiling == (before.ceiling * (1 + WPI_CHANGE_2026)).quantize(Decimal("0.01"))


def test_the_lowered_ceiling_puts_exactly_the_three_dearest_brands_over_it(priced):
    _, scenario, items = priced
    brands = sorted(
        (item for item in items.values() if item.molecule == scenario.lowered.molecule),
        key=lambda item: -item.mrp,
    )
    assert scenario.over_ceiling == {item.id for item in brands[:3]}
    limit = scenario.lowered.max_retail_price(brands[0].gst_rate)
    assert brands[2].mrp > limit >= brands[3].mrp


def test_only_the_dearest_brands_and_the_price_rise_are_overcharged(priced):
    business, scenario, items = priced
    exposure = overcharge_exposure(
        business.ledger, business.batches, items, scenario.ceilings, as_of=END
    )
    above = {o.sale.batch.item_id for o in exposure.overcharges if o.cause is Cause.ABOVE_CEILING}
    sold_after_revision = {
        m.batch.item_id
        for m in business.ledger
        if m.kind is MovementType.SALE
        and m.batch.item_id in scenario.over_ceiling
        and ist_date(m.at) >= REVISION_DAY
    }
    assert above == sold_after_revision
    assert above, "at least one over-ceiling brand kept selling"
    assert all(
        o.sale.at.date() >= REVISION_DAY
        for o in exposure.overcharges
        if o.cause is Cause.ABOVE_CEILING
    )
    rises = [o for o in exposure.overcharges if o.cause is Cause.PRICE_RISE]
    assert {o.sale.batch for o in rises} == {scenario.price_rise}
    assert sum(o.units for o in rises) == scenario.price_rise_units_sold
    assert exposure.interest > 0


def test_the_price_rise_batch_is_eighteen_percent_dearer(priced):
    business, scenario, items = priced
    batch = business.batches[scenario.price_rise]
    item = items[batch.key.item_id]
    assert not item.dpco_scheduled
    assert batch.mrp == (item.mrp * Decimal("1.18")).quantize(Decimal("0.01"))


def test_needs_a_brand_of_the_molecule_to_lower():
    business = simulate(SimConfig(start=date(2026, 1, 1), days=40, n_chemists=20))
    with pytest.raises(LookupError, match="no scheduled Unobtainium"):
        seed_price_control(business, lowered_molecule="Unobtainium")


@pytest.mark.parametrize(("start", "days"), [(date(2026, 1, 1), 40), (date(2026, 6, 1), 30)])
def test_a_simulation_that_cannot_hold_the_rise_and_its_sales_gets_ceilings_and_no_rise(
    start, days
):
    business = simulate(SimConfig(start=start, days=days, n_chemists=20))
    movements, batches = len(business.ledger), dict(business.batches)
    scenario = seed_price_control(business)
    assert (scenario.price_rise, scenario.price_rise_units_sold) == (None, 0)
    assert (len(business.ledger), business.batches) == (movements, batches)
    assert scenario.lowered.reference == "SIM/NPPA/2026/017"


def test_everything_seeded_falls_within_the_simulated_period(priced):
    business, scenario, _ = priced
    rise = [m for m in business.ledger if m.batch == scenario.price_rise]
    assert rise and all(date(2026, 1, 1) <= ist_date(m.at) < END for m in rise)


def test_the_ceiling_goes_a_paisa_lower_when_rounding_up_would_reach_the_third_brand():
    # Seed 600's third and fourth dearest Atorvastatin brands are a paisa apart.
    business = simulate(SimConfig(start=date(2026, 6, 1), days=1, seed=600, n_chemists=5))
    scenario = seed_price_control(business)
    brands = sorted(
        (i for i in business.catalogue.items if i.molecule == scenario.lowered.molecule),
        key=lambda item: -item.mrp,
    )
    assert brands[2].mrp - brands[3].mrp == Decimal("0.01")
    assert scenario.over_ceiling == {item.id for item in brands[:3]}
    assert scenario.lowered.max_retail_price(brands[0].gst_rate) == brands[3].mrp


def test_another_formulation_is_lowered_when_the_named_brands_cannot_be_separated():
    # Seed 315: 70.04 and 70.03 with 5% GST; ceilings allow 70.02 or 70.04, never 70.03.
    business = simulate(SimConfig(start=date(2026, 6, 1), days=1, seed=315, n_chemists=5))
    scenario = seed_price_control(business)
    assert scenario.lowered.molecule != "Atorvastatin"
    brands = sorted(
        (i for i in business.catalogue.items if i.molecule == scenario.lowered.molecule),
        key=lambda item: -item.mrp,
    )
    assert scenario.over_ceiling == {item.id for item in brands[:3]}
    lowered = [p for p in scenario.ceilings if p.effective_from == REVISION_DAY]
    assert [p.reference for p in lowered].count("SIM/NPPA/2026/017") == 1


def test_a_catalogue_with_no_formulation_to_separate_is_refused():
    business = simulate(SimConfig(start=date(2026, 6, 1), days=1, seed=315, n_chemists=5))
    atorvastatin = [i for i in business.catalogue.items if i.molecule == "Atorvastatin"]
    business.catalogue = replace(business.catalogue, items=tuple(atorvastatin))
    with pytest.raises(ValueError, match="exactly three above it"):
        seed_price_control(business)
