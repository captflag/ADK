from datetime import date, timedelta

import pytest

from batchward.agents.data import StockData, use
from batchward.sim.business import SimConfig, simulate
from batchward.sim.scenarios import seed_recall

START = date(2026, 1, 1)
DAYS = 90


@pytest.fixture(scope="session")
def simulated():
    """Three months of trading with the AZ4021 recall seeded in. Read it, never change it."""
    business = simulate(SimConfig(start=START, days=DAYS, n_chemists=60))
    recall = seed_recall(business)
    data = StockData(
        parties={p.id: p for p in [*business.catalogue.companies, *business.chemists]},
        items={item.id: item for item in business.catalogue.items},
        batches=business.batches,
        ledger=business.ledger,
        locations=business.locations,
        today=START + timedelta(days=DAYS),
    )
    return data, recall


@pytest.fixture
def stock(simulated):
    data, _ = simulated
    with use(data):
        yield data


@pytest.fixture
def recall(simulated):
    return simulated[1]
