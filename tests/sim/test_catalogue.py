import pytest

from batchward.core.models import PartyKind, Schedule
from batchward.sim.catalogue import MOLECULES, build_catalogue


def test_same_seed_gives_the_same_catalogue():
    assert build_catalogue(seed=11) == build_catalogue(seed=11)


def test_different_seeds_give_different_catalogues():
    assert build_catalogue(seed=11).items != build_catalogue(seed=12).items


def test_generates_the_requested_companies_with_unique_ids_and_names():
    catalogue = build_catalogue(n_companies=35)
    assert len(catalogue.companies) == 35
    assert len({c.id for c in catalogue.companies}) == 35
    assert len({c.name for c in catalogue.companies}) == 35
    assert all(c.kind is PartyKind.COMPANY for c in catalogue.companies)


def test_every_item_belongs_to_a_generated_company():
    catalogue = build_catalogue()
    company_ids = {c.id for c in catalogue.companies}
    assert {item.company_id for item in catalogue.items} <= company_ids


def test_item_ids_are_unique_and_every_item_has_a_molecule():
    catalogue = build_catalogue()
    ids = [item.id for item in catalogue.items]
    assert len(ids) == len(set(ids))
    assert set(catalogue.molecules) == set(ids)


def test_each_company_carries_a_molecule_at_most_once():
    catalogue = build_catalogue()
    seen = [(item.company_id, item.molecule) for item in catalogue.items]
    assert len(seen) == len(set(seen))


def test_brands_per_company_stay_within_the_requested_range():
    catalogue = build_catalogue(brands_per_company=(3, 5))
    for company in catalogue.companies:
        count = sum(item.company_id == company.id for item in catalogue.items)
        assert 3 <= count <= 5


def test_items_inherit_regulatory_flags_from_their_molecule():
    catalogue = build_catalogue()
    for item in catalogue.items:
        molecule = catalogue.molecules[item.id]
        assert item.schedules == molecule.schedules
        assert item.cold_chain == molecule.cold_chain
        assert item.dpco_scheduled == molecule.dpco_scheduled


def test_catalogue_includes_cold_chain_and_schedule_h1_items():
    catalogue = build_catalogue()
    assert any(item.cold_chain for item in catalogue.items)
    assert any(Schedule.H1 in item.schedules for item in catalogue.items)


def test_brand_names_read_like_pharma_brands():
    catalogue = build_catalogue()
    azithro = next(i for i in catalogue.items if i.molecule == "Azithromycin")
    stem, strength = azithro.brand.split()
    assert stem.startswith("Azi")
    assert strength == "500"


@pytest.mark.parametrize("bad", [(0, 5), (5, 3), (1, len(MOLECULES) + 1)])
def test_rejects_an_impossible_brand_range(bad):
    with pytest.raises(ValueError, match="brands_per_company"):
        build_catalogue(brands_per_company=bad)


def test_every_company_has_the_licence_and_address_a_purchase_record_needs():
    for company in build_catalogue().companies:
        assert company.drug_licence_no.startswith("SIM/MFG/")
        assert company.address.startswith("Plot ")
