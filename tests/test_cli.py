import sqlite3
from contextlib import closing

from batchward.bridge.marg_contract import check_layout
from batchward.bridge.marg_import import read_ledger, read_masters
from batchward.cli import main
from batchward.core.trace import trace_batch

COVERS_RECALL = ["--start", "2026-01-01", "--days", "45", "--chemists", "40"]


def open_db(path):
    return closing(sqlite3.connect(path))


def test_writes_a_marg_database_with_the_recall_seeded(tmp_path, capsys):
    output = tmp_path / "demo" / "marg.sqlite"
    assert main(["demo-marg", str(output), *COVERS_RECALL]) == 0

    with open_db(output) as connection:
        assert check_layout(connection) == []
        masters = read_masters(connection)
        ledger = read_ledger(connection, masters)
    recalled = next(key for key in masters.batches if key.batch_no == "AZ4021")
    assert len(trace_batch(ledger, recalled).recipients) == 38

    printed = capsys.readouterr().out
    assert f"Wrote {output}" in printed
    assert "batch AZ4021, supplied to 38 chemists, 210 strips on hand" in printed
    assert not output.with_name("marg.sqlite.partial").exists()


def test_skips_the_recall_when_the_simulation_misses_its_dates(tmp_path, capsys):
    output = tmp_path / "marg.sqlite"
    assert main(["demo-marg", str(output), "--start", "2026-03-01", "--days", "5"]) == 0
    with open_db(output) as connection:
        batch_numbers = {key.batch_no for key in read_masters(connection).batches}
    assert "AZ4021" not in batch_numbers
    assert "No recall seeded" in capsys.readouterr().out


def test_refuses_to_overwrite_without_force(tmp_path, capsys):
    output = tmp_path / "marg.sqlite"
    output.write_text("keep me")
    assert main(["demo-marg", str(output), "--days", "2"]) == 1
    assert output.read_text() == "keep me"
    assert "pass --force" in capsys.readouterr().err


def test_force_replaces_an_existing_file(tmp_path):
    output = tmp_path / "marg.sqlite"
    output.write_text("old")
    assert main(["demo-marg", str(output), "--start", "2026-03-01", "--days", "2", "--force"]) == 0
    with open_db(output) as connection:
        assert check_layout(connection) == []


def test_backtest_scores_every_method_against_the_naive_benchmark(capsys):
    assert main(["backtest", "--days", "196", "--min-history", "20"]) == 0
    printed = capsys.readouterr().out
    assert "28 weeks from 2024-09-02" in printed
    assert "(benchmark)" in printed
    assert "smooth:" in printed
    for method in ("moving average (8 weeks)", "exponential smoothing", "Croston (SBA)", "TSB"):
        assert method in printed


def test_backtest_refuses_a_run_too_short_to_forecast(capsys):
    assert main(["backtest", "--days", "70", "--min-history", "26"]) == 1
    assert "at least 27 are needed" in capsys.readouterr().err
