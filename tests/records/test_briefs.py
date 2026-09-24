import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime

import pytest

from batchward.records.store import MIGRATIONS, KeptBrief, RecordsError, RecordStore

MONDAY = KeptBrief(date(2026, 9, 21), datetime(2026, 9, 21, 2, 30, tzinfo=UTC), "Monday's brief")
TUESDAY = KeptBrief(date(2026, 9, 22), datetime(2026, 9, 22, 2, 30, tzinfo=UTC), "Tuesday's")


def test_briefs_are_kept_in_the_order_made_and_the_latest_is_read_back(tmp_path):
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        assert store.latest_brief() is None
        store.save_brief(MONDAY)
        store.save_brief(TUESDAY)
        again = KeptBrief(MONDAY.day, datetime(2026, 9, 22, 3, tzinfo=UTC), "Monday's, sent late")
        store.save_brief(again)
    with RecordStore(path, create=False) as store:
        assert store.briefs() == [MONDAY, TUESDAY, again]
        assert store.latest_brief() == again


def test_a_brief_is_never_changed_or_kept_twice(tmp_path):
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        store.save_brief(MONDAY)
        with pytest.raises(RecordsError, match="cannot record the brief for 2026-09-21"):
            store.save_brief(MONDAY)
        with pytest.raises(ValueError, match="timezone-aware"):
            store.save_brief(KeptBrief(MONDAY.day, datetime(2026, 9, 21, 8), "naive"))
    with closing(sqlite3.connect(path)) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="only ever added to"):
            connection.execute("UPDATE briefs SET body = 'edited'")
        with pytest.raises(sqlite3.IntegrityError, match="only ever added to"):
            connection.execute("DELETE FROM briefs")


def test_a_database_from_the_eighth_schema_gains_the_briefs_table(tmp_path):
    path = tmp_path / "records.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.executescript(
            f"BEGIN; {''.join(MIGRATIONS[:8])} PRAGMA user_version = 8; COMMIT;"
        )
    with RecordStore(path) as store:
        assert store.briefs() == []
        store.save_brief(TUESDAY)
        assert store.latest_brief() == TUESDAY
