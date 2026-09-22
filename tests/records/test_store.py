import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from batchward.compliance.prices import CeilingPrice
from batchward.core.models import BatchStatus
from batchward.records.store import MIGRATIONS, SCHEMA_VERSION, RecordsError, RecordStore
from recall_factories import NOTICE, RECALLED

LOOK_ALIKE = replace(RECALLED, batch_no="AZ4O21")


@pytest.fixture
def path(tmp_path):
    return tmp_path / "records.sqlite"


def place(log, batch=RECALLED, hours=0):
    return log.place(
        batch,
        BatchStatus.BLOCKED,
        at=NOTICE.received_at + timedelta(hours=hours),
        reason="Class I recall",
        reference=NOTICE.reference,
        placed_by="system",
    )


def test_a_notice_reads_back_exactly_as_recorded(path):
    with RecordStore(path) as store:
        assert store.save_notice(NOTICE) is True
    with RecordStore(path) as store:
        assert store.notice(NOTICE.reference) == NOTICE
        assert store.notice("RN/0000") is None
        assert store.notices() == [NOTICE]


def test_a_notice_without_optional_details_reads_back_too(path):
    bare = replace(NOTICE, reference="CDSCO/NSQ/57", manufacturer=None, product=None, expiry=None)
    with RecordStore(path) as store:
        store.save_notice(bare)
        assert store.notice(bare.reference) == bare


def test_recording_the_same_notice_twice_is_harmless(path):
    with RecordStore(path) as store:
        store.save_notice(NOTICE)
        assert store.save_notice(NOTICE) is False
        assert len(store.notices()) == 1


def test_a_different_notice_under_a_used_reference_is_refused(path):
    with RecordStore(path) as store:
        store.save_notice(NOTICE)
        with pytest.raises(RecordsError, match="different notice is already recorded"):
            store.save_notice(replace(NOTICE, batch_no="AZ4022"))


def test_finds_notices_by_batch_number_ignoring_spaces_and_case_only(path):
    other = replace(NOTICE, reference="RN/2026/015", batch_no="AZ4O21")
    with RecordStore(path) as store:
        store.save_notice(NOTICE)
        store.save_notice(other)
        assert store.notices_naming(" az 4021") == [NOTICE]
        assert store.notices_naming("AZ4O21") == [other]


def test_holds_and_releases_read_back_into_a_working_log(path):
    with RecordStore(path) as store:
        log = store.hold_log()
        blocked, other = place(log), place(log, LOOK_ALIKE, hours=1)
        release = log.release(
            other.id,
            at=other.at + timedelta(hours=2),
            reason="wrong batch",
            released_by="pharmacist",
        )
        for hold in (blocked, other):
            store.save_hold(hold)
        store.save_release(release)

    with RecordStore(path) as store:
        log = store.hold_log()
    assert list(log) == [blocked, other]
    assert log.release_of(other.id) == release
    assert log.active(RECALLED) == [blocked]
    assert log.active(LOOK_ALIKE) == []
    assert place(log).id == "H00003", "new holds continue the numbering"


def test_the_database_refuses_to_edit_or_delete_any_record(path):
    with RecordStore(path) as store:
        store.save_notice(NOTICE)
        log = store.hold_log()
        hold = place(log)
        store.save_hold(hold)
        store.save_release(
            log.release(hold.id, at=hold.at, reason="checked", released_by="pharmacist")
        )

    with closing(sqlite3.connect(path)) as connection:
        for statement in (
            "UPDATE notices SET batch_no = 'AZ4022'",
            "DELETE FROM notices",
            "UPDATE holds SET status = 'live'",
            "DELETE FROM holds",
            "UPDATE releases SET reason = 'never happened'",
            "DELETE FROM releases",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="only ever added to"):
                connection.execute(statement)


def test_a_release_needs_its_hold_and_a_hold_id_is_used_once(path):
    with RecordStore(path) as store:
        log = store.hold_log()
        hold = place(log)
        store.save_hold(hold)
        with pytest.raises(RecordsError, match="cannot record hold H00001"):
            store.save_hold(hold)
        orphan = replace(
            log.release(hold.id, at=hold.at, reason="x", released_by="y"), hold_id="H09999"
        )
        with pytest.raises(RecordsError, match="cannot record release of hold H09999"):
            store.save_release(orphan)


def test_a_transaction_records_everything_or_nothing(path):
    with RecordStore(path) as store:
        with pytest.raises(RuntimeError), store.transaction():
            store.save_notice(NOTICE)
            store.save_hold(place(store.hold_log()))
            raise RuntimeError("crash before commit")
        assert store.notices() == []
        assert list(store.hold_log()) == []

        with store.transaction():
            store.save_notice(NOTICE)
        assert store.notices() == [NOTICE]


def test_transactions_do_not_nest(path):
    with (
        RecordStore(path) as store,
        store.transaction(),
        pytest.raises(RecordsError, match="cannot be nested"),
    ):
        store.transaction().__enter__()


def test_marks_the_schema_version_and_refuses_newer_or_foreign_databases(tmp_path, path):
    RecordStore(path).close()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RecordsError, match="newer Batchward"):
        RecordStore(path)

    foreign = tmp_path / "marg.sqlite"
    with closing(sqlite3.connect(foreign)) as connection:
        connection.execute("CREATE TABLE PRO (CODE TEXT)")
    with pytest.raises(RecordsError, match="not a Batchward records database"):
        RecordStore(foreign)


ATORVASTATIN = CeilingPrice(
    "Atorvastatin", "10 mg", "strip of 10 tablets", Decimal("64.21"), date(2026, 4, 1), "N/17"
)


def test_ceiling_prices_read_back_as_a_working_table(path):
    earlier = replace(ATORVASTATIN, ceiling=Decimal("70.25"), effective_from=date(2023, 4, 1))
    with RecordStore(path) as store:
        assert store.save_ceiling(ATORVASTATIN) is True
        assert store.save_ceiling(earlier) is True
    with RecordStore(path) as store:
        assert store.ceiling_prices() == [earlier, ATORVASTATIN]
        table = store.ceiling_table()
    assert len(table) == 2


def test_the_same_ceiling_twice_is_harmless_but_a_different_one_for_the_date_is_refused(path):
    with RecordStore(path) as store:
        store.save_ceiling(ATORVASTATIN)
        assert store.save_ceiling(replace(ATORVASTATIN, molecule="ATORVASTATIN")) is False
        with pytest.raises(RecordsError, match="different ceiling price"):
            store.save_ceiling(replace(ATORVASTATIN, ceiling=Decimal("60.00")))
        assert len(store.ceiling_prices()) == 1


def test_ceiling_prices_are_never_edited_or_deleted(path):
    with RecordStore(path) as store:
        store.save_ceiling(ATORVASTATIN)
    with closing(sqlite3.connect(path)) as connection:
        for statement in ("UPDATE ceiling_prices SET ceiling = '1'", "DELETE FROM ceiling_prices"):
            with pytest.raises(sqlite3.IntegrityError, match="only ever added to"):
                connection.execute(statement)


def test_a_database_from_the_first_schema_is_brought_up_to_date_and_keeps_its_records(path):
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.executescript(f"BEGIN; {MIGRATIONS[0]} PRAGMA user_version = 1; COMMIT;")
        connection.execute(
            "INSERT INTO notices VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                NOTICE.reference,
                NOTICE.source,
                str(NOTICE.recall_class),
                NOTICE.received_at.isoformat(),
                NOTICE.batch_no,
                NOTICE.manufacturer,
                NOTICE.product,
                NOTICE.expiry.isoformat(),
            ),
        )
    with RecordStore(path) as store:
        assert store.notices() == [NOTICE]
        store.save_ceiling(ATORVASTATIN)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)


def test_many_connections_opening_a_new_file_at_once_all_succeed(tmp_path):
    import threading

    path = tmp_path / "records.sqlite"
    ready = threading.Barrier(12)
    errors = []

    def open_and_save():
        try:
            ready.wait()
            with RecordStore(path) as store:
                store.save_ceiling(ATORVASTATIN)
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=open_and_save) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with RecordStore(path) as store:
        assert store.ceiling_prices() == [ATORVASTATIN]


def test_a_file_that_is_not_a_database_or_is_a_folder_is_refused_clearly(tmp_path):
    junk = tmp_path / "junk.sqlite"
    junk.write_text("this is not a database")
    with pytest.raises(RecordsError, match="not a usable records database"):
        RecordStore(junk)
    with pytest.raises(RecordsError, match="cannot be opened"):
        RecordStore(tmp_path)


def test_reading_only_never_creates_a_records_file(tmp_path):
    missing = tmp_path / "typo.sqlite"
    with pytest.raises(RecordsError, match="no records database"):
        RecordStore(missing, create=False)
    assert not missing.exists()
    empty = tmp_path / "empty.sqlite"
    empty.touch()
    with pytest.raises(RecordsError, match="holds no Batchward records"):
        RecordStore(empty, create=False)
    assert empty.stat().st_size == 0


def test_a_file_claiming_the_schema_version_without_its_tables_is_refused(tmp_path):
    other = tmp_path / "other.sqlite"
    with closing(sqlite3.connect(other)) as connection:
        connection.execute("CREATE TABLE things (id INTEGER)")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    with pytest.raises(RecordsError, match="not a Batchward records database"):
        RecordStore(other)


def test_a_database_locked_by_another_writer_is_a_records_error(tmp_path):
    path = tmp_path / "records.sqlite"
    RecordStore(path).close()
    with closing(sqlite3.connect(path, timeout=0, isolation_level=None)) as other:
        other.execute("BEGIN IMMEDIATE")
        store = RecordStore(path)
        store._connection.execute("PRAGMA busy_timeout = 0")
        with store, pytest.raises(RecordsError, match="busy"):
            store.save_notice(NOTICE)
        other.execute("ROLLBACK")


def test_formulations_that_differ_only_by_where_a_bar_falls_are_kept_apart(tmp_path):
    day = date(2026, 4, 1)
    one = CeilingPrice("Amoxicillin|Clavulanate", "500 mg", "strip", Decimal(10), day, "N1")
    two = CeilingPrice("Amoxicillin", "Clavulanate|500 mg", "strip", Decimal(20), day, "N2")
    with RecordStore(tmp_path / "records.sqlite") as store:
        assert store.save_ceiling(one) and store.save_ceiling(two)
        assert len(store.ceiling_prices()) == 2


def test_the_same_ceiling_spaced_differently_is_recognised_as_already_recorded(tmp_path):
    day = date(2026, 4, 1)
    price = CeilingPrice("Atorvastatin", "10 mg", "strip of 10 tablets", Decimal(5), day, "N1")
    with RecordStore(tmp_path / "records.sqlite") as store:
        assert store.save_ceiling(price)
        assert store.save_ceiling(replace(price, strength="10MG")) is False
        with pytest.raises(RecordsError, match="different ceiling price"):
            store.save_ceiling(replace(price, strength="10MG", ceiling=Decimal(6)))


def test_a_stored_row_that_no_longer_makes_a_record_is_a_records_error(tmp_path):
    path = tmp_path / "records.sqlite"
    RecordStore(path).close()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO ceiling_prices VALUES ('x', 'X', '1 mg', 'vial', '0', '2026-04-01', 'N')"
        )
        connection.commit()
    with RecordStore(path) as store, pytest.raises(RecordsError, match="cannot be read"):
        store.ceiling_prices()
