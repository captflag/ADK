import sqlite3
from contextlib import closing
from datetime import date

import pytest

from batchward.bridge.marg_export import export_to_marg
from batchward.bridge.marg_import import read_ledger, read_masters
from batchward.sim.business import SimConfig, simulate
from batchward.sim.scenarios import seed_recall


@pytest.fixture
def connection():
    """An empty in-memory database standing in for a Marg installation."""
    with closing(sqlite3.connect(":memory:")) as conn:
        yield conn


@pytest.fixture(scope="session")
def seeded():
    """Two months of trading with the AZ4021 recall seeded in.

    Shared by every bridge test, so tests must read it and never change it.
    """
    b = simulate(SimConfig(start=date(2026, 1, 1), days=60, n_chemists=60))
    return b, seed_recall(b)


@pytest.fixture(scope="session")
def business(seeded):
    return seeded[0]


@pytest.fixture(scope="session")
def recall(seeded):
    """The seeded AZ4021 recall, with the answer a trace must reproduce."""
    return seeded[1]


@pytest.fixture(scope="session")
def business_records(business):
    """The business as the keyword arguments ``export_to_marg`` takes."""
    return {
        "parties": [*business.catalogue.companies, *business.chemists],
        "items": business.catalogue.items,
        "batches": business.batches,
        "ledger": business.ledger,
    }


@pytest.fixture(scope="session")
def marg_template(business_records):
    """The business exported to Marg tables once, for tests to copy."""
    with closing(sqlite3.connect(":memory:")) as conn:
        export_to_marg(conn, **business_records)
        yield conn


@pytest.fixture(scope="session")
def rebuilt(marg_template):
    """The whole ledger replayed from Marg once; tests must only read it."""
    return read_ledger(marg_template, read_masters(marg_template))


@pytest.fixture
def exported(marg_template):
    """A private copy of the exported Marg database; a test may change it freely."""
    with closing(sqlite3.connect(":memory:")) as conn:
        marg_template.backup(conn)
        yield conn
