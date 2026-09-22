import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from batchward.agents import data as agent_data
from batchward.agents.data import DataUnavailableError, current, load_marg, records_path, use
from batchward.bridge.marg_export import export_to_marg
from batchward.core.clock import ist_date


@pytest.fixture(scope="module")
def marg_file(tmp_path_factory, simulated):
    stock, _ = simulated
    path = tmp_path_factory.mktemp("marg") / "marg.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        export_to_marg(
            connection,
            parties=stock.parties.values(),
            items=stock.items.values(),
            batches=stock.batches,
            ledger=stock.ledger,
        )
    return path


@pytest.fixture(scope="module")
def loaded(marg_file):
    """The database loaded once for the tests that only read it."""
    return load_marg(marg_file)


def test_loads_a_marg_database_and_dates_it_the_day_after_the_last_movement(loaded, simulated):
    stock, _ = simulated
    assert loaded.ledger.balances() == stock.ledger.balances()
    last = ist_date(max(m.at for m in stock.ledger))
    assert loaded.today == last + timedelta(days=1)


def test_the_returns_shelf_is_unsellable_by_default(loaded):
    sellable = {location.id: location.sellable for location in loaded.locations}
    assert sellable["RETURNS"] is False
    assert sellable["GODOWN"] is True


def test_refuses_a_today_before_the_last_recorded_movement(marg_file, loaded):
    with pytest.raises(ValueError, match="cannot be before"):
        load_marg(marg_file, today=loaded.today - timedelta(days=5))


def test_a_missing_database_is_reported_as_unavailable(tmp_path):
    with pytest.raises(DataUnavailableError, match="no Marg database"):
        load_marg(tmp_path / "missing.sqlite")


def test_current_explains_how_to_configure_data_when_none_is_set(monkeypatch):
    monkeypatch.delenv("BATCHWARD_MARG_DB", raising=False)
    monkeypatch.setattr(agent_data, "_installed", None)
    with pytest.raises(DataUnavailableError, match="BATCHWARD_MARG_DB"):
        current()


def test_current_loads_the_configured_database(monkeypatch, marg_file):
    monkeypatch.setattr(agent_data, "_installed", None)
    monkeypatch.setenv("BATCHWARD_MARG_DB", str(marg_file))
    assert current().ledger.balances()


def test_use_installs_data_only_for_the_block(simulated, monkeypatch):
    stock, _ = simulated
    monkeypatch.setattr(agent_data, "_installed", None)
    monkeypatch.delenv("BATCHWARD_MARG_DB", raising=False)
    with use(stock):
        assert current() is stock
    with pytest.raises(DataUnavailableError):
        current()


def test_records_come_from_the_environment_unless_installed(simulated, monkeypatch, tmp_path):
    stock, _ = simulated
    monkeypatch.setenv("BATCHWARD_RECORDS", str(tmp_path / "env.sqlite"))
    assert records_path() == tmp_path / "env.sqlite"
    with use(stock, records=tmp_path / "installed.sqlite"):
        assert records_path() == tmp_path / "installed.sqlite"
    with use(stock):
        assert records_path() is None, "installed data without records must not read the env"
    monkeypatch.delenv("BATCHWARD_RECORDS")
    assert records_path() is None


def test_a_file_that_is_not_a_database_is_reported_as_unavailable(tmp_path):
    junk = tmp_path / "marg.sqlite"
    junk.write_text("this is not a database")
    with pytest.raises(DataUnavailableError, match="not a readable Marg database"):
        load_marg(junk)
