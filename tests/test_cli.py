import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from batchward import price_cli
from batchward.bridge.marg_contract import check_layout
from batchward.bridge.marg_import import read_ledger, read_masters
from batchward.buying.planning import plan_for
from batchward.channels import whatsapp
from batchward.claims.submission import draft_for
from batchward.cli import main
from batchward.core.approvals import Approval
from batchward.core.clock import IST, ist_date
from batchward.core.models import MovementType, StockMovement
from batchward.core.orders import PurchaseOrder
from batchward.core.trace import trace_batch
from batchward.records.store import RecordStore

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


def test_demo_marg_leaves_no_partial_file_when_marg_cannot_hold_the_data(
    tmp_path, capsys, monkeypatch
):
    def refuse(connection, **records):
        connection.execute("CREATE TABLE half_written (x)")
        raise ValueError("Marg stores expiry as MM/YYYY; 2027-10-15 is not a month end")

    monkeypatch.setattr("batchward.cli.export_to_marg", refuse)
    output = tmp_path / "marg.sqlite"
    assert main(["demo-marg", str(output), "--start", "2026-03-01", "--days", "2"]) == 1
    assert "not a month end" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_demo_marg_with_force_replaces_old_records_even_with_no_recall_or_ceilings(
    tmp_path, monkeypatch, capsys
):
    def no_ceilings(business):
        raise ValueError("no scheduled formulation's brands are priced far enough apart")

    # Two days in March miss the recall and seed no ceilings; the return terms remain.
    monkeypatch.setattr("batchward.cli.seed_price_control", no_ceilings)
    marg, records = tmp_path / "marg.sqlite", tmp_path / "records.sqlite"
    records.write_text("from an earlier run")
    args = ["--start", "2026-03-01", "--days", "2", "--chemists", "5", "--force"]
    assert main(["demo-marg", str(marg), *args, "--records", str(records)]) == 0
    assert "Recorded return terms for 35 companies" in capsys.readouterr().out
    with RecordStore(records, create=False) as store:
        assert (store.notices(), store.ceiling_prices()) == ([], [])
        assert len(store.return_terms()) == 35


def test_demo_marg_seeds_no_price_rise_the_simulation_cannot_hold(tmp_path, capsys):
    output = tmp_path / "marg.sqlite"
    args = ["--start", "2026-01-01", "--days", "40", "--chemists", "5"]
    assert main(["demo-marg", str(output), *args]) == 0
    with open_db(output) as connection:
        masters = read_masters(connection)
        ledger = read_ledger(connection, masters)
    assert max(ist_date(m.at) for m in ledger) == date(2026, 2, 9)
    assert "PR2601" not in {key.batch_no for key in masters.batches}
    printed = capsys.readouterr().out
    assert "ceiling is lowered on 01/04/2026 below 3 brands" in printed
    assert "No price rise seeded: it needs the simulation to run from 2026-02-02 to 2026-03-24" in (
        printed
    )


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


def test_health_reports_stock_dead_stock_expiry_and_consumption(capsys):
    assert main(["health", "--days", "120", "--top", "3"]) == 0
    printed = capsys.readouterr().out
    assert "Stock health on 30 December 2025" in printed
    for section in ("Stock at cost", "Dead stock", "Expiry risk", "Consumption at cost"):
        assert section in printed
    assert "₹" in printed


def test_recall_drill_prints_the_report_and_passes(capsys):
    assert main(["recall-drill", "--start", "2026-01-01", "--days", "10", "--chemists", "60"]) == 0
    printed = capsys.readouterr().out
    assert "RECALL: STOCK POSITION AND RECONCILIATION" in printed
    assert "Supplied to 38 chemists" in printed
    assert "AZ4O21, expiry 10/2027" in printed
    assert "FAIL" not in printed
    assert printed.rstrip().endswith("Drill passed.")


def test_recall_drill_needs_enough_chemists(capsys):
    assert main(["recall-drill", "--days", "1", "--chemists", "20"]) == 1
    assert "at least 38 chemists" in capsys.readouterr().err


def test_demo_marg_can_record_the_seeded_notice_and_block_the_batch(tmp_path, capsys):
    marg, records = tmp_path / "marg.sqlite", tmp_path / "records.sqlite"
    assert main(["demo-marg", str(marg), "--records", str(records), *COVERS_RECALL]) == 0
    assert "Received notice RN/2026/014 on 12/02/2026 09:15 IST and blocked 1 batch" in (
        capsys.readouterr().out
    )
    with RecordStore(records) as store:
        (notice,) = store.notices()
        (hold,) = list(store.hold_log())
    assert (notice.batch_no, hold.batch.batch_no, hold.placed_by) == ("AZ4021", "AZ4021", "system")


def test_demo_marg_refuses_to_overwrite_records_without_force(tmp_path, capsys):
    records = tmp_path / "records.sqlite"
    records.write_text("keep me")
    marg = tmp_path / "marg.sqlite"
    assert main(["demo-marg", str(marg), "--records", str(records), "--days", "2"]) == 1
    assert records.read_text() == "keep me"
    assert not marg.exists()


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    folder = tmp_path_factory.mktemp("recall")
    marg, records = folder / "marg.sqlite", folder / "records.sqlite"
    assert main(["demo-marg", str(marg), *COVERS_RECALL]) == 0
    return ["--marg", str(marg), "--records", str(records)]


NOTICE_ARGS = [
    "--reference", "RN/2026/014",
    "--source", "the manufacturer",
    "--class", "I",
    "--batch", "az 4021",
    "--expiry", "10/2027",
    "--received", "2026-02-12T09:15",
]  # fmt: skip


def notice_args(demo, **names):
    with open_db(demo[1]) as connection:
        masters = read_masters(connection)
    recalled = next(key for key in masters.batches if key.batch_no == "AZ4021")
    item = masters.items[recalled.item_id]
    company = masters.parties[recalled.company_id]
    return [
        *NOTICE_ARGS,
        "--manufacturer", names.get("manufacturer", f"M/s. {company.name} Ltd."),
        "--product", names.get("product", f"{item.molecule} Tablets IP {item.strength}"),
    ]  # fmt: skip


def test_recall_commands_receive_report_release_and_list(demo, capsys):
    capsys.readouterr()
    assert main(["recall", "receive", *demo, *notice_args(demo)]) == 0
    received = capsys.readouterr().out
    assert "received 12/02/2026 09:15 IST" in received
    assert "Blocked automatically at " in received
    assert "  H00001  AZ4021, expiry 10/2027" in received

    assert main(["recall", "receive", *demo, *notice_args(demo)]) == 0
    assert "Already recorded" in capsys.readouterr().out

    status = ["recall", "status", *demo, "--reference", "RN/2026/014"]
    assert main(status) == 0
    report = capsys.readouterr().out
    assert "Supplied to 38 chemists" in report
    # Recorded today for a notice that arrived in February: the block is honestly late.
    assert "Stop sale             MET LATE" in report
    assert "Hold H00001 on AZ4021, in force" in report

    release = ["--hold", "H00001", "--reason", "notice withdrawn", "--by", "pharmacist"]
    records = demo[3]
    assert main(["recall", "release", "--records", records, *release]) == 0
    assert "Released hold H00001 at " in capsys.readouterr().out

    assert main(["recall", "list", "--records", records]) == 0
    listed = capsys.readouterr().out
    assert "RN/2026/014  Class I  batch az 4021" in listed
    assert "H00001  blocked AZ4021  released" in listed

    assert main(["recall", "release", "--records", records, *release]) == 1
    assert "already released" in capsys.readouterr().err


def test_a_notice_that_only_resembles_a_batch_blocks_nothing(tmp_path_factory, demo, capsys):
    records = str(tmp_path_factory.mktemp("other") / "records.sqlite")
    args = [demo[0], demo[1], "--records", records]
    wrong = notice_args(demo, manufacturer="M/s. Someone Else Pharma")
    capsys.readouterr()
    assert main(["recall", "receive", *args, *wrong]) == 0
    out = capsys.readouterr().out
    assert "nothing was blocked" in out
    assert "Raised for review, not blocked:" in out
    assert "but the batch is from" in out


def test_recall_status_explains_an_unknown_reference(demo, capsys):
    assert main(["recall", "status", *demo, "--reference", "RN/0000"]) == 1
    assert "no recall notice recorded under reference RN/0000" in capsys.readouterr().err


def test_recall_commands_explain_a_missing_marg_database(tmp_path, capsys):
    args = ["--marg", str(tmp_path / "none.sqlite"), "--records", str(tmp_path / "r.sqlite")]
    assert main(["recall", "receive", *args, *NOTICE_ARGS]) == 1
    assert "no Marg database" in capsys.readouterr().err


def test_price_guard_blocks_the_dearest_brands_and_reports_exposure(capsys):
    args = ["--start", "2026-02-01", "--days", "90", "--chemists", "40"]
    assert main(["price-guard", *args]) == 0
    printed = capsys.readouterr().out
    assert "SIM/NPPA/2026/017 lowered the ceiling for Atorvastatin 10 mg" in printed
    assert "  BLOCK  " in printed
    assert "printed MRP above the ceiling price in force:" in printed
    assert "MRP rose more than 10% in twelve months:" in printed
    assert "is 18.0% above" in printed
    assert "Exposure including interest: ₹" in printed


@pytest.mark.parametrize(
    "window",
    [["--days", "1"], ["--start", "2026-06-01", "--days", "30"], ["--start", "2020-01-01"]],
)
def test_price_guard_runs_without_a_price_rise_the_simulation_cannot_hold(window, capsys):
    assert main(["price-guard", "--days", "5", *window, "--chemists", "10"]) == 0
    printed = capsys.readouterr().out
    assert "PR2601" not in printed
    assert "No price rise seeded: it needs the simulation to run from 02/02/2026 to 24/03/2026" in (
        printed
    )


def test_price_guard_counts_stock_sold_in_the_last_second_before_billing(monkeypatch, capsys):
    seed = price_cli.seed_price_control

    def seed_then_sell_everything(business):
        scenario = seed(business)
        last_second = datetime(2026, 2, 1, 23, 59, 59, 500_000, tzinfo=IST)
        for number, ((key, location), units) in enumerate(business.ledger.balances().items()):
            sale = StockMovement(
                f"L{number}", last_second, MovementType.SALE, key, location, -units, "L"
            )
            business.ledger.append(sale)
        return scenario

    monkeypatch.setattr("batchward.price_cli.seed_price_control", seed_then_sell_everything)
    assert main(["price-guard", "--start", "2026-02-01", "--days", "1", "--chemists", "5"]) == 0
    assert "  0 blocked, 0 to check by hand, 0 allowed" in capsys.readouterr().out


def test_price_guard_suggests_another_seed_when_the_scenario_does_not_fit_it(monkeypatch, capsys):
    def cannot_fit(business):
        raise ValueError("no scheduled formulation's brands are priced far enough apart")

    monkeypatch.setattr("batchward.price_cli.seed_price_control", cannot_fit)
    assert main(["price-guard", "--seed", "315", "--days", "1", "--chemists", "5"]) == 1
    error = capsys.readouterr().err
    assert "priced far enough apart" in error
    assert "try another --seed" in error


def test_ceilings_are_recorded_listed_and_protected_from_contradiction(tmp_path, capsys):
    records = str(tmp_path / "records.sqlite")
    add = [
        "ceilings", "add", "--records", records,
        "--molecule", "Atorvastatin", "--strength", "10 mg", "--unit", "strip of 10 tablets",
        "--effective", "2026-04-01", "--reference", "S.O. 1234(E)",
    ]  # fmt: skip
    assert main([*add, "--ceiling", "64.21"]) == 0
    assert "Recorded a ceiling of ₹64.21 without GST for Atorvastatin 10 mg" in (
        capsys.readouterr().out
    )
    assert main([*add, "--ceiling", "64.21"]) == 0
    assert "Already recorded" in capsys.readouterr().out
    assert main([*add, "--ceiling", "60.00"]) == 1
    assert "different ceiling price" in capsys.readouterr().err

    assert main(["ceilings", "list", "--records", records]) == 0
    listed = capsys.readouterr().out
    assert (
        "01/04/2026       ₹64.21  Atorvastatin 10 mg, strip of 10 tablets  (S.O. 1234(E))" in listed
    )


def test_demo_marg_records_the_simulated_ceiling_prices(tmp_path, capsys):
    marg, records = tmp_path / "marg.sqlite", tmp_path / "records.sqlite"
    args = ["--start", "2026-01-01", "--days", "60", "--chemists", "40", "--records", str(records)]
    assert main(["demo-marg", str(marg), *args]) == 0
    printed = capsys.readouterr().out
    assert (
        "Seeded price problems: the Atorvastatin 10 mg ceiling is lowered on 01/04/2026" in printed
    )
    with RecordStore(records) as store:
        prices = store.ceiling_prices()
    assert f"Recorded {len(prices)} ceiling prices" in printed
    assert {p.reference for p in prices} >= {"SIM/NPPA/2023/CEILINGS", "SIM/NPPA/2026/017"}


def test_registrar_reports_complete_records_then_the_gaps_an_edit_introduces(tmp_path, capsys):
    marg = tmp_path / "marg.sqlite"
    assert main(["demo-marg", str(marg), *COVERS_RECALL]) == 0
    capsys.readouterr()
    assert main(["registrar", "--marg", str(marg)]) == 0
    printed = capsys.readouterr().out
    assert "Every memo and purchase record carries the particulars Rule 65(5) requires." in printed
    assert "Not checked: the competent person's signature" in printed

    with open_db(marg) as connection:
        connection.execute('UPDATE "ORDER" SET ADDRESS = NULL WHERE CODE = ?', ("CH001",))
        connection.execute('UPDATE "ORDER" SET DLNO = NULL WHERE CODE = ?', ("C01",))
        connection.commit()
    assert main(["registrar", "--marg", str(marg), "--top", "3"]) == 0
    printed = capsys.readouterr().out
    assert "buyer's address missing" in printed
    assert "supplier's licence number missing" in printed
    assert "gaps an inspector would find:" in printed


def test_registrar_explains_a_missing_database(tmp_path, capsys):
    assert main(["registrar", "--marg", str(tmp_path / "none.sqlite")]) == 1
    assert "no Marg database" in capsys.readouterr().err


def exit_code(args):
    """What a person running the command sees: a return code, or argparse's exit status."""
    try:
        return main([str(arg) for arg in args])
    except SystemExit as stopped:
        return stopped.code


@pytest.fixture
def junk(tmp_path):
    not_a_database = tmp_path / "junk.sqlite"
    not_a_database.write_text("this is not a database")
    folder = tmp_path / "folder"
    folder.mkdir()
    return not_a_database, folder


def test_bad_input_is_refused_with_a_message_never_a_traceback(tmp_path, junk, capsys):
    not_a_database, folder = junk
    ceiling = ["ceilings", "add", "--records", tmp_path / "c.sqlite", "--molecule", "M",
               "--strength", "1 mg", "--unit", "vial", "--effective", "2026-04-01",
               "--reference", "R"]  # fmt: skip
    notice = ["--reference", "RN/X", "--source", "S", "--class", "I", "--batch", "AZ4021"]
    cases = [
        (["demo-marg", tmp_path / "a.sqlite", "--chemists", "0"], 2),
        (["demo-marg", tmp_path / "b.sqlite", "--days", "-5"], 2),
        (["demo-marg", folder, "--days", "1", "--chemists", "10", "--force"], 1),
        (["recall-drill", "--days", "1", "--chemists", "5000"], 1),
        (["price-guard", "--days", "1", "--chemists", "5000"], 1),
        ([*ceiling, "--ceiling", "NaN"], 2),
        ([*ceiling, "--ceiling", "Infinity"], 2),
        ([*ceiling[:5], " ", *ceiling[6:], "--ceiling", "5"], 1),
        (["ceilings", "list", "--records", not_a_database], 1),
        (["recall", "list", "--records", not_a_database], 1),
        (["recall", "list", "--records", folder], 1),
        (
            [
                "recall",
                "receive",
                "--marg",
                not_a_database,
                "--records",
                tmp_path / "r.sqlite",
                *notice,
            ],
            1,
        ),
        (["registrar", "--marg", not_a_database], 1),
    ]
    for args, expected in cases:
        assert exit_code(args) == expected, args
        assert "Traceback" not in capsys.readouterr().err
    assert (
        not (tmp_path / "c.sqlite").exists()
        or not RecordStore(tmp_path / "c.sqlite").ceiling_prices()
    )


def test_a_notice_received_again_without_its_time_is_recognised(tmp_path_factory, demo, capsys):
    records = str(tmp_path_factory.mktemp("again") / "records.sqlite")
    args = [demo[0], demo[1], "--records", records]
    first = [a for a in notice_args(demo) if a not in ("--received", "2026-02-12T09:15")]
    assert main(["recall", "receive", *args, *first]) == 0
    capsys.readouterr()
    assert main(["recall", "receive", *args, *first]) == 0
    assert "Already recorded" in capsys.readouterr().out


def test_times_in_the_future_are_refused(tmp_path, demo, capsys):
    records = str(tmp_path / "records.sqlite")
    future = [a if a != "2026-02-12T09:15" else "2999-01-01T09:00" for a in notice_args(demo)]
    assert main(["recall", "receive", demo[0], demo[1], "--records", records, *future]) == 1
    assert "is in the future" in capsys.readouterr().err


def test_listing_or_reporting_never_creates_a_records_file(tmp_path, demo, capsys):
    missing = tmp_path / "typo.sqlite"
    for command in (
        ["ceilings", "list", "--records", str(missing)],
        ["recall", "list", "--records", str(missing)],
        ["recall", "status", demo[0], demo[1], "--records", str(missing), "--reference", "X"],
    ):
        assert main(command) == 1
        assert "no records database" in capsys.readouterr().err
    assert not missing.exists()


def test_recall_notices_draft_one_notice_per_chemist_still_holding_the_batch(
    tmp_path, demo, capsys
):
    records = str(tmp_path / "records.sqlite")
    stock = [demo[0], demo[1], "--records", records]
    assert main(["recall", "receive", *stock, *notice_args(demo)]) == 0
    notices = ["recall", "notices", *stock, "--reference", "RN/2026/014"]
    out = tmp_path / "notices"
    capsys.readouterr()

    assert main([*notices, "--out", str(out), "--from", "Godavari Pharma, Nagpur"]) == 0
    written = sorted(out.iterdir())
    assert len(written) == 38
    assert "Drafted 38 notices to chemists still holding 790 units" in capsys.readouterr().out
    text = written[0].read_text(encoding="utf-8")
    assert text.startswith("RECALL NOTICE TO CHEMIST")
    assert "From      Godavari Pharma, Nagpur" in text

    assert main([*notices, "--out", str(out)]) == 1
    assert "pass --force to overwrite them" in capsys.readouterr().err
    assert main([*notices, "--out", str(out), "--force"]) == 0

    capsys.readouterr()
    assert main(notices) == 0
    printed = capsys.readouterr()
    assert printed.out.count("RECALL NOTICE TO CHEMIST") == 38
    assert "Nothing has been sent" in printed.err


NOTIFICATION = "\n".join(
    [
        "Sl. No.,Name of the Scheduled Formulation,Dosage form & Strength,Unit,Ceiling Price (Rs.)",
        "1,Atorvastatin,Tablet 10 mg,1 Tablet,6.42",
        "2,Insulin glargine,Injection 100 IU/ml,1 ml,86.00",
        "3,Ibuprofen,Tablet 400 mg,1 Tablet,1.07",
    ]
)


def test_ceilings_import_records_a_notification_per_pack_stocked(tmp_path, demo, capsys):
    table = tmp_path / "so-1234.csv"
    table.write_text(NOTIFICATION, encoding="utf-8")
    records = tmp_path / "records.sqlite"
    run = [
        "ceilings", "import", "--records", str(records), "--marg", demo[1], "--csv", str(table),
        "--notification", "S.O. 1234(E)", "--effective", "2026-10-01",
    ]  # fmt: skip

    assert main([*run, "--dry-run"]) == 0
    assert "Would record 1 ceiling prices" in capsys.readouterr().out
    assert not records.exists()

    assert main(run) == 0
    printed = capsys.readouterr().out
    assert "Atorvastatin 10 mg, strip of 10 tablets: ₹64.20 without GST" in printed
    assert "1 notified formulation matches no item stocked" in printed
    assert "Insulin glargine 100 IU/ml, prefilled pen" in printed
    assert main(["ceilings", "list", "--records", str(records)]) == 0
    assert "01/10/2026       ₹64.20  Atorvastatin 10 mg" in capsys.readouterr().out

    assert main(run) == 0
    assert "Recorded 0 ceiling prices for the packs stocked (1 already recorded)" in (
        capsys.readouterr().out
    )
    table.write_text(NOTIFICATION.replace("6.42", "6.50"), encoding="utf-8")
    assert main(run) == 1
    assert "a different ceiling price" in capsys.readouterr().err


def test_recall_import_alerts_records_each_row_and_blocks_only_exact_matches(
    tmp_path, demo, capsys
):
    with open_db(demo[1]) as connection:
        masters = read_masters(connection)
    recalled = next(key for key in masters.batches if key.batch_no == "AZ4021")
    item, company = masters.items[recalled.item_id], masters.parties[recalled.company_id]
    product, maker = f"{item.molecule} Tablets IP {item.strength}", f"M/s. {company.name} Ltd."
    alerts = tmp_path / "alerts.csv"
    alerts.write_text(
        "\n".join(
            [
                "S. No.,Name of Drugs/medical device/cosmetic,Batch No.,Date of Manufacture,"
                "Date of Expiry,Manufactured By,NSQ Result",
                f'1,{product},AZ4021,Nov-2025,Oct-2027,"{maker}",Dissolution',
                f'2,{product},AZ4O21,Nov-2025,Oct-2027,"{maker}",Assay',
                '3,Paracetamol Tablets IP 650 mg,PCM9981,01/2026,12/2028,"M/s. X Pharma",Assay',
            ]
        ),
        encoding="utf-8",
    )
    records = tmp_path / "records.sqlite"
    run = [
        "recall", "import-alerts", demo[0], demo[1], "--records", str(records),
        "--csv", str(alerts), "--list", "CDSCO drug alert, August 2026", "--class", "II",
    ]  # fmt: skip
    capsys.readouterr()

    assert main(run) == 0
    printed = capsys.readouterr().out
    assert "CDSCO drug alert, August 2026: 3 rows, 3 recorded as new notices" in printed
    assert "  #1  H00001  AZ4021, expiry 10/2027" in printed
    assert "  #2  AZ4021, expiry 10/2027" in printed
    assert "is easily misread as it" in printed
    assert "No batch ever held resembles the other 1 rows." in printed

    assert main(run) == 0
    again = capsys.readouterr().out
    assert "0 recorded as new notices, 3 already recorded" in again
    assert "H00002" not in again

    assert main(["recall", "list", "--records", str(records)]) == 0
    assert "CDSCO drug alert, August 2026 #1  Class II  batch AZ4021" in capsys.readouterr().out


def test_demo_invoices_check_as_ready_and_a_misread_one_is_sent_back(tmp_path, capsys):
    marg, invoices = tmp_path / "marg.sqlite", tmp_path / "invoices"
    args = ["--start", "2026-01-01", "--days", "20", "--chemists", "10"]
    assert main(["demo-marg", str(marg), *args, "--invoices", str(invoices)]) == 0
    assert "supplier invoices from 01/01/2026 to 20/01/2026" in capsys.readouterr().out
    written = sorted(invoices.glob("*.json"))
    assert written

    check = ["intake", "check", str(written[0]), "--marg", str(marg), "--received", "2026-01-20"]
    assert main(check) == 0
    assert "Ready to post" in capsys.readouterr().out

    text = written[0].read_text(encoding="utf-8")
    grand = json.loads(text)["grand_total"]
    written[0].write_text(text.replace(f'"{grand}"', '"1.00"'), encoding="utf-8")
    assert main(check) == 0
    printed = capsys.readouterr().out
    assert "Extract again: these fields do not read or do not add up:" in printed
    assert "invoice, grand_total:" in printed
    assert "Not ready to post." in printed

    written[0].write_text("{}", encoding="utf-8")
    assert main(check) == 1
    assert "is not an invoice in the intake schema" in capsys.readouterr().err


def test_intake_extract_reads_checks_and_writes_the_reading(tmp_path, monkeypatch, capsys):
    from batchward.intake.extract import Extraction
    from batchward.intake.invoice import PurchaseInvoice

    marg, invoices = tmp_path / "marg.sqlite", tmp_path / "invoices"
    args = ["--start", "2026-01-01", "--days", "20", "--chemists", "10"]
    assert main(["demo-marg", str(marg), *args, "--invoices", str(invoices)]) == 0
    printed = sorted(invoices.glob("*.txt"))[0]
    truth = PurchaseInvoice.model_validate_json(printed.with_suffix(".json").read_bytes())
    sent = []

    async def read_perfectly(document, *, check):
        sent.append(document)
        return Extraction(truth, check(truth), attempts=1)

    monkeypatch.setattr("batchward.intake_cli.extract_invoice", read_perfectly)
    extract = ["intake", "extract", str(printed), "--marg", str(marg), "--received", "2026-01-20"]
    out = tmp_path / "reading.json"
    capsys.readouterr()

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    assert main(extract) == 1
    assert "needs a Gemini API key" in capsys.readouterr().err

    monkeypatch.setenv("GOOGLE_API_KEY", "not-a-real-key")
    assert main([*extract, "--out", str(out)]) == 0
    shown = capsys.readouterr().out
    assert f"Read {printed.name} in 1 attempt." in shown
    assert "Ready to post" in shown
    assert PurchaseInvoice.model_validate_json(out.read_bytes()) == truth
    assert sent[0].text.startswith(truth.supplier_name.upper())


@pytest.fixture(scope="module")
def deliveries(tmp_path_factory):
    """A simulated month of deliveries, with the stock, records and paperwork to receive them."""
    folder = tmp_path_factory.mktemp("deliveries")
    marg, records, invoices = folder / "marg.sqlite", folder / "rec.sqlite", folder / "inv"
    args = ["--start", "2026-01-01", "--days", "30", "--chemists", "40"]
    demo = ["demo-marg", str(marg), *args, "--invoices", str(invoices), "--records", str(records)]
    assert main(demo) == 0
    return folder


def receiving(deliveries, tmp_path, bill=0):
    """Fresh records, and `intake receive` arguments for one simulated bill, count aside."""
    records = tmp_path / "rec.sqlite"
    shutil.copy(deliveries / "rec.sqlite", records)
    invoices = deliveries / "inv"
    reading = sorted(path for path in invoices.glob("*.json") if ".order" not in path.name)[bill]
    order_no = json.loads(reading.read_text(encoding="utf-8"))["order_no"]
    receive = [
        "intake", "receive", str(reading), "--marg", str(deliveries / "marg.sqlite"),
        "--records", str(records), "--order", str(invoices / (order_no.replace("/", "-") +
        ".order.json")), "--received", "2026-01-30",
    ]  # fmt: skip
    return receive, reading, records


def short_count(reading, tmp_path, units=3):
    count = reading.with_suffix(".count.csv")
    lines = count.read_text(encoding="utf-8").splitlines()
    first = lines[1].rsplit(",", 1)
    short = tmp_path / "short.csv"
    short.write_text("\n".join([lines[0], f"{first[0]},{int(first[1]) - units}", *lines[2:]]))
    return short


def test_intake_receive_asks_for_approval_and_posts_once_approved(deliveries, tmp_path, capsys):
    receive, reading, records = receiving(deliveries, tmp_path)
    count, short = reading.with_suffix(".count.csv"), short_count(reading, tmp_path)
    posted = tmp_path / "posted"
    approvals = ["approvals", "--records", str(records)]
    capsys.readouterr()

    assert main([*receive, "--count", str(count)]) == 0
    printed = capsys.readouterr().out
    assert "Matched against order" in printed
    assert "Nothing written. Put it up for approval with --out" in printed

    assert main([*receive, "--count", str(short), "--approve-by", "Ravi"]) == 1
    assert "asking for approval needs --out" in capsys.readouterr().err

    assert main([*receive, "--count", str(short), "--out", str(posted)]) == 0
    printed = capsys.readouterr().out
    assert "3 billed units" in printed and "Put up for approval as A-0001" in printed
    assert not posted.exists()
    assert main([*receive, "--count", str(short), "--out", str(posted)]) == 0
    assert "Already waiting for approval as A-0001." in capsys.readouterr().out

    assert main(["approvals", "list", "--records", str(records)]) == 0
    assert "A-0001" in capsys.readouterr().out
    assert main(["approvals", "show", "a-0001", "--records", str(records)]) == 0
    printed = capsys.readouterr().out
    assert "Now: waiting." in printed and "DEBIT NOTE" in printed and "SUPPLIER GSTIN" in printed

    assert main(["approvals", "approve", "A-0001", "--records", str(records), "--by", "Ravi"]) == 0
    assert "Approved by Ravi." in capsys.readouterr().out
    written = sorted(path.name for path in posted.iterdir())
    assert [name.split(".", 1)[1] for name in written] == ["debit-note.txt", "marg-purchase.csv"]
    assert main([*approvals[:1], "approve", "A-0001", *approvals[1:], "--by", "Asha"]) == 1
    assert "A-0001 is approved (by Ravi on" in capsys.readouterr().err

    assert (
        main([*receive, "--count", str(short), "--approve-by", "Ravi", "--out", str(posted)]) == 0
    )
    assert "Already approved by Ravi" in capsys.readouterr().out
    assert (
        main([*receive, "--count", str(count), "--approve-by", "Asha", "--out", str(posted)]) == 1
    )
    assert "already approved by Ravi" in capsys.readouterr().err
    with RecordStore(records) as store:
        (request,) = store.requests()
        assert (store.decision("A-0001").decided_by, str(store.request_state(request))) == (
            "Ravi",
            "approved",
        )


def test_a_request_replaced_by_a_recount_and_then_rejected_writes_nothing(
    deliveries, tmp_path, capsys
):
    receive, reading, records = receiving(deliveries, tmp_path)
    count, short = reading.with_suffix(".count.csv"), short_count(reading, tmp_path)
    posted = tmp_path / "posted"
    capsys.readouterr()

    assert main([*receive, "--count", str(count), "--out", str(posted)]) == 0
    assert main([*receive, "--count", str(short), "--out", str(posted)]) == 0
    printed = capsys.readouterr().out
    assert "A-0001, waiting for the same in another form, is replaced." in printed
    assert main(["approvals", "approve", "A-0001", "--records", str(records), "--by", "Ravi"]) == 1
    assert "A-0001 is replaced" in capsys.readouterr().err

    reject = ["approvals", "reject", "A-0002", "--records", str(records), "--by", "Asha"]
    assert main([*reject, "--reason", "  "]) == 1
    assert main([*reject, "--reason", "count it again"]) == 0
    assert "A-0002 rejected by Asha: count it again. Nothing written." in capsys.readouterr().out
    assert not posted.exists()
    assert main(["approvals", "list", "--records", str(records)]) == 0
    assert "Nothing is waiting for approval." in capsys.readouterr().out
    assert main(["approvals", "list", "--records", str(records), "--all"]) == 0
    printed = capsys.readouterr().out
    assert "[replaced]" in printed and "[rejected by Asha]" in printed

    assert (
        main([*receive, "--count", str(count), "--approve-by", "Ravi", "--out", str(posted)]) == 0
    )
    assert "Put up for approval as A-0003" in capsys.readouterr().out
    assert sorted(path.suffix for path in posted.iterdir()) == [".csv"]


def test_a_delivery_changed_after_it_was_put_up_is_not_posted(deliveries, tmp_path, capsys):
    receive, reading, records = receiving(deliveries, tmp_path)
    count = tmp_path / "count.csv"
    shutil.copy(reading.with_suffix(".count.csv"), count)
    posted = tmp_path / "posted"
    assert main([*receive, "--count", str(count), "--out", str(posted)]) == 0
    count.write_text(short_count(reading, tmp_path, units=5).read_text())
    capsys.readouterr()

    assert main(["approvals", "approve", "A-0001", "--records", str(records), "--by", "Ravi"]) == 1
    assert "has changed since it was put up for approval" in capsys.readouterr().err
    assert not posted.exists()
    assert main(["approvals", "list", "--records", str(records), "--all"]) == 0
    assert "[approved, not carried out by Ravi]" in capsys.readouterr().out
    assert main(["approvals", "show", "A-0001", "--records", str(records)]) == 0
    assert "Note: not posted:" in capsys.readouterr().out


def test_intake_receive_catches_an_order_billed_again_on_a_later_bill(deliveries, tmp_path, capsys):
    receive, reading, _ = receiving(deliveries, tmp_path)
    first = json.loads(reading.read_text(encoding="utf-8"))
    again = tmp_path / "again.json"
    again.write_text(json.dumps({**first, "invoice_no": first["invoice_no"] + "A"}), "utf-8")
    receive = [*receive[:2], *receive[3:], "--count", str(reading.with_suffix(".count.csv"))]
    approve = ["--approve-by", "Ravi", "--out", str(tmp_path / "posted")]
    capsys.readouterr()

    assert main([*receive, str(reading), *approve]) == 0
    printed = capsys.readouterr().out
    assert "Earlier" in printed and "Approved by Ravi." in printed

    assert main([*receive, str(again)]) == 0
    printed = capsys.readouterr().out
    assert "already received on earlier bills" in printed
    assert "Not ready to post." in printed
    assert main([*receive, str(again), *approve]) == 1
    assert "not ready to post" in capsys.readouterr().err

    assert main([*receive, str(reading), *approve]) == 0
    assert "Already approved by Ravi" in capsys.readouterr().out


def test_answering_a_request_that_does_not_exist_is_refused(deliveries, tmp_path, capsys):
    _, _, records = receiving(deliveries, tmp_path)
    assert main(["approvals", "approve", "A-0099", "--records", str(records), "--by", "R"]) == 1
    assert "no request A-0099" in capsys.readouterr().err
    assert main(["approvals", "list", "--records", str(tmp_path / "none.sqlite")]) == 1
    assert "no records database" in capsys.readouterr().err


def test_claims_from_windows_to_approval_to_credit_note(deliveries, tmp_path, capsys):
    records, marg = tmp_path / "rec.sqlite", deliveries / "marg.sqlite"
    shutil.copy(deliveries / "rec.sqlite", records)
    stock = ["--marg", str(marg), "--records", str(records)]
    out = tmp_path / "claims"
    capsys.readouterr()

    assert main(["claims", "windows", *stock]) == 0
    printed = capsys.readouterr().out
    assert "Claim now: ₹" in printed and "Written off in the last 90 days" in printed
    windows = draft_for(marg, records).windows
    company = next(w.company_id for w in windows if w.state in ("open", "closing soon"))

    assert main(["claims", "draft", company, *stock, "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "Put up for approval as A-0001" in printed and not out.exists()
    assert main(["approvals", "approve", "A-0001", "--records", str(records), "--by", "Ravi"]) == 0
    assert "Approved by Ravi." in capsys.readouterr().out
    assert sorted(path.name.split(".", 1)[1] for path in out.iterdir()) == [
        "claim-letter.txt",
        "claim.csv",
        "marg-purchase-return.csv",
    ]
    assert main(["claims", "draft", company, *stock, "--out", str(out)]) == 0
    assert "Nothing can be claimed from" in capsys.readouterr().out

    with RecordStore(records) as store:
        (claim,) = store.claims()
    listing = ["claims", "list", "--records", str(records), "--on", "2026-03-02"]
    assert main(listing) == 0
    assert f"{claim.number}" in capsys.readouterr().out
    settle = ["claims", "settle", claim.number.lower(), "--records", str(records)]
    note = [*settle, "--credit-note", "CN-77", "--received", "2026-03-01", "--by", "Ravi"]
    assert main([*note, "--amount", "ten"]) == 1
    assert "is not an amount" in capsys.readouterr().err
    assert main([*settle, "--credit-note", "CN-1", "--amount", "1", "--received", "2025-01-01",
                 "--by", "Ravi"]) == 1  # fmt: skip
    assert "cannot arrive before claim" in capsys.readouterr().err
    assert main([*note, "--amount", f"{claim.total:,}"]) == 0
    assert "The claim is settled in full." in capsys.readouterr().out
    assert main(listing) == 0
    assert "No claims are waiting for credit." in capsys.readouterr().out


def test_claims_terms_are_imported_and_listed(tmp_path, capsys):
    records, terms = tmp_path / "rec.sqlite", tmp_path / "terms.csv"
    terms.write_text(
        "Company,Opens,Closes,Credit %,Effective From,Reference\n"
        "C01,90,30,100,01/04/2026,Area manager letter\n",
        encoding="utf-8",
    )
    command = ["claims", "terms", "import", str(terms), "--records", str(records)]
    assert main(command) == 0
    assert main(command) == 0
    assert "Recorded 0 of 1 return terms; 1 already recorded." in capsys.readouterr().out
    assert main(["claims", "terms", "list", "--records", str(records)]) == 0
    assert "Area manager letter" in capsys.readouterr().out
    terms.write_text("Company,Credit\nC01,100\n", encoding="utf-8")
    assert main(command) == 1
    assert "no column" in capsys.readouterr().err


def test_whatsapp_notify_sends_a_waiting_request_to_every_approver(
    deliveries, tmp_path, capsys, monkeypatch
):
    receive, reading, records = receiving(deliveries, tmp_path)
    count = reading.with_suffix(".count.csv")
    approvers = tmp_path / "approvers.csv"
    approvers.write_text("919812345678,Ravi\n919000000002,Asha\n", encoding="utf-8")
    for name in ("TOKEN", "PHONE_NUMBER_ID", "APP_SECRET", "VERIFY_TOKEN"):
        monkeypatch.delenv(f"WHATSAPP_{name}", raising=False)
    notify = ["whatsapp", "notify", "A-0001", "--records", str(records)]
    capsys.readouterr()

    assert main([*receive, "--count", str(count), "--out", str(tmp_path / "out"), "--notify",
                 "--approvers", str(approvers)]) == 1  # fmt: skip
    printed = capsys.readouterr()
    assert "Put up for approval as A-0001" in printed.out
    assert "set WHATSAPP_TOKEN" in printed.err

    for name in ("TOKEN", "PHONE_NUMBER_ID", "APP_SECRET", "VERIFY_TOKEN"):
        monkeypatch.setenv(f"WHATSAPP_{name}", "test")
    sent = []

    def fake(request, people, settings, **_):
        sent.extend(people)
        return {
            name: None if phone.endswith("678") else "refused" for phone, name in people.items()
        }

    monkeypatch.setattr("batchward.whatsapp_cli.notify", fake)
    assert main([*notify, "--approvers", str(approvers)]) == 0
    printed = capsys.readouterr().out
    assert "Ravi: sent" in printed and "Asha: refused" in printed
    assert "Sent A-0001 to 1 of 2 approvers on WhatsApp." in printed
    assert sent == ["919812345678", "919000000002"]
    monkeypatch.delenv("BATCHWARD_APPROVERS", raising=False)
    assert main(notify) == 1
    assert "who may approve" in capsys.readouterr().err
    assert main([*notify[:2], "A-0404", *notify[3:], "--approvers", str(approvers)]) == 1
    assert "no request A-0404" in capsys.readouterr().err


def test_the_brief_is_printed_and_sent_with_each_request_waiting_for_approval(
    deliveries, tmp_path, capsys, monkeypatch
):
    receive, reading, records = receiving(deliveries, tmp_path)
    count = str(reading.with_suffix(".count.csv"))
    assert main([*receive, "--count", count, "--out", str(tmp_path / "out")]) == 0
    for name in ("MARG_DB", "RECORDS", "APPROVERS"):
        monkeypatch.delenv(f"BATCHWARD_{name}", raising=False)
    for name in ("TOKEN", "PHONE_NUMBER_ID", "APP_SECRET", "VERIFY_TOKEN"):
        monkeypatch.delenv(f"WHATSAPP_{name}", raising=False)
    for name in ("APPROVAL", "BRIEF"):
        monkeypatch.delenv(f"WHATSAPP_{name}_TEMPLATE", raising=False)
    brief = ["brief", "--marg", str(deliveries / "marg.sqlite"), "--records", str(records)]
    approvers = tmp_path / "approvers.csv"
    approvers.write_text("919812345678,Ravi\n919000000002,Asha\n", encoding="utf-8")
    capsys.readouterr()

    assert main(brief) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("Batchward brief for Sat 31/01/2026\n1. ")
    assert "\nWaiting for approval:\n- A-0001 (purchase voucher, " in printed
    assert main(["brief"]) == 1
    assert "give --marg or set BATCHWARD_MARG_DB" in capsys.readouterr().err
    assert main([*brief[:3], "--send"]) == 1
    assert "sending needs --records" in capsys.readouterr().err
    assert main([*brief, "--send", "--approvers", str(approvers)]) == 1
    assert "set WHATSAPP_TOKEN" in capsys.readouterr().err

    for name in ("TOKEN", "PHONE_NUMBER_ID", "APP_SECRET", "VERIFY_TOKEN"):
        monkeypatch.setenv(f"WHATSAPP_{name}", "test")
    with RecordStore(records) as store:
        assert store.briefs() == []
        wrote = datetime.now(UTC) - timedelta(hours=1)
        store.save_message("whatsapp", "wamid.1", "919812345678", "hello", wrote)
    sent = []

    def record(settings, message):
        sent.append(message)

    real_brief, real_notify = whatsapp.send_brief, whatsapp.notify
    monkeypatch.setattr(
        "batchward.brief_cli.send_brief", lambda *a, **k: real_brief(*a, **k, sender=record)
    )
    monkeypatch.setattr(
        "batchward.brief_cli.notify", lambda *a, **k: real_notify(*a, **k, sender=record)
    )
    assert main([*brief, "--send", "--approvers", str(approvers)]) == 0
    printed = capsys.readouterr().out
    assert "  Ravi: sent\n  Asha: has not written to the business number" in printed
    assert "set WHATSAPP_BRIEF_TEMPLATE in .env" in printed
    assert "Sent the brief to 1 of 2 approvers on WhatsApp." in printed
    assert "Sent A-0001, with its buttons, to 1 of 2 approvers." in printed
    assert [(message["to"], message["type"]) for message in sent] == [
        ("+919812345678", "text"),
        ("+919812345678", "interactive"),
    ]
    with RecordStore(records, create=False) as store:
        (kept,) = store.briefs()
    assert sent[0]["text"]["body"] == kept.text and printed.startswith(kept.text)
    assert kept.day == date(2026, 1, 31)


def test_orders_are_suggested_drafted_approved_and_then_counted_as_coming(
    deliveries, tmp_path, capsys
):
    records, marg = tmp_path / "rec.sqlite", deliveries / "marg.sqlite"
    shutil.copy(deliveries / "rec.sqlite", records)
    stock = ["--marg", str(marg), "--records", str(records)]
    out = tmp_path / "orders"
    capsys.readouterr()

    assert main(["orders", "suggest", *stock, "--limit", "3"]) == 0
    assert "To order on 31/01/2026, covering 4 days' lead time" in capsys.readouterr().out
    wanted = [s for s in plan_for(marg, records).suggestions if s.quantity > 0]
    company = wanted[0].item.company_id

    assert (
        main(["orders", "draft", company, *stock, "--out", str(out), "--approve-by", "Ravi"]) == 0
    )
    printed = capsys.readouterr().out
    assert f"Order PO/{company}/260131" in printed and "Approved by Ravi." in printed
    assert sorted(path.suffix for path in out.iterdir()) == [".csv", ".txt"]
    assert main(["orders", "list", "--records", str(records), "--on", "2026-02-03"]) == 0
    assert f"PO/{company}/260131" in capsys.readouterr().out
    assert main(["orders", "draft", company, *stock, "--out", str(out)]) == 0
    assert "Nothing needs ordering from" in capsys.readouterr().out
    assert main(["orders", "draft", "C99", *stock, "--out", str(out)]) == 1
    assert "no company C99" in capsys.readouterr().err


def test_intake_receive_finds_the_order_an_invoice_quotes_in_the_records(
    deliveries, tmp_path, capsys
):
    receive, reading, records = receiving(deliveries, tmp_path)
    order_file = receive[receive.index("--order") + 1]
    without_order = [a for a in receive if a not in ("--order", order_file)]
    order = PurchaseOrder.from_json(Path(order_file).read_bytes())
    with RecordStore(records) as store:
        store.save_approval(
            Approval(f"order:{order.number}", "purchase order", "Ravi",
                     datetime(2026, 1, 10, tzinfo=IST), "0" * 64, "placed through Batchward")
        )  # fmt: skip
        store.save_order(order, f"order:{order.number}")
    capsys.readouterr()
    count = str(reading.with_suffix(".count.csv"))
    assert main([*without_order, "--count", count]) == 0
    printed = capsys.readouterr().out
    assert f"Matched against order {order.number}." in printed and "Ready to post." in printed
