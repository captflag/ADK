from datetime import date

from batchward.sim.business import SimConfig, simulate
from batchward.sim.catalogue import build_catalogue
from batchward.sim.return_terms import return_terms


def test_every_company_has_terms_the_same_every_time():
    catalogue = build_catalogue()
    first = return_terms(catalogue, effective_from=date(2024, 1, 1))
    assert [t.company_id for t in first] == [c.id for c in catalogue.companies]
    assert first == return_terms(catalogue, effective_from=date(2024, 1, 1))
    assert len({(t.opens_days_before_expiry, t.credit_percent) for t in first}) > 1


def test_making_terms_changes_nothing_the_simulation_produces():
    config = SimConfig(start=date(2026, 1, 1), days=5, n_chemists=10)
    before = list(simulate(config).ledger)
    return_terms(build_catalogue(config.seed), effective_from=date(2024, 1, 1))
    assert list(simulate(config).ledger) == before
