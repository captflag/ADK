import sqlite3
from contextlib import closing
from datetime import date

import pytest

from batchward.sim.business import SimConfig, simulate
from batchward.sim.scenarios import seed_recall


@pytest.fixture
def connection():
    """An empty in-memory database standing in for a Marg installation."""
    with closing(sqlite3.connect(":memory:")) as conn:
        yield conn


@pytest.fixture(scope="module")
def business():
    """Two months of trading with the AZ4021 recall seeded in."""
    b = simulate(SimConfig(start=date(2026, 1, 1), days=60, n_chemists=60))
    seed_recall(b)
    return b


@pytest.fixture(scope="module")
def business_records(business):
    """The business as the keyword arguments ``export_to_marg`` takes."""
    return {
        "parties": [*business.catalogue.companies, *business.chemists],
        "items": business.catalogue.items,
        "batches": business.batches,
        "ledger": business.ledger,
    }
