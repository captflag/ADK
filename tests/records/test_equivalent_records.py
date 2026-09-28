"""Keeping what a person said a notified formulation means, and correcting it."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from batchward.compliance.equivalents import Equivalent
from batchward.core.clock import IST
from batchward.records.store import RecordStore

NOTED = datetime(2026, 9, 28, 11, 0, tzinfo=IST)


def equivalent(**rest) -> Equivalent:
    fields = {
        "formulation": "Amoxycillin and Potassium Clavulanate",
        "strength": "500 mg + 125 mg",
        "molecule": "Amoxicillin + Clavulanic acid",
        "item_strength": "625 mg",
        "noted_by": "Divyansh",
        "noted_at": NOTED,
        "note": "the schedule's name for our 625",
    } | rest
    return Equivalent(**fields)


@pytest.fixture
def store(tmp_path):
    with RecordStore(tmp_path / "records.sqlite") as open_store:
        yield open_store


def test_an_equivalence_is_kept_as_it_was_typed(store):
    assert store.save_equivalent(equivalent()) is True
    (kept,) = store.equivalents()
    assert kept == equivalent()
    assert kept.formulation == "Amoxycillin and Potassium Clavulanate"


def test_recording_the_same_thing_twice_records_nothing_new(store):
    store.save_equivalent(equivalent())
    assert store.save_equivalent(equivalent(noted_at=NOTED + timedelta(days=1))) is False
    assert len(store.equivalents()) == 1


def test_the_same_meaning_typed_differently_is_the_same_record(store):
    store.save_equivalent(equivalent())
    same = equivalent(
        formulation="AMOXYCILLIN AND POTASSIUM CLAVULANATE",
        strength="500mg+125mg",
        molecule="amoxicillin + clavulanic acid",
        noted_at=NOTED + timedelta(days=2),
    )
    assert store.save_equivalent(same) is False
    assert len(store.equivalents()) == 1


def test_a_correction_supersedes_without_writing_over_what_was_recorded(store):
    store.save_equivalent(equivalent())
    corrected = equivalent(
        molecule="Amoxicillin",
        noted_at=NOTED + timedelta(days=1),
        note="the 625 is the combination, not this",
    )
    assert store.save_equivalent(corrected) is True

    kept = store.equivalents()
    assert len(kept) == 2, "both records stay on file"
    assert [record.molecule for record in kept] == ["Amoxicillin + Clavulanic acid", "Amoxicillin"]
    in_force = store.equivalent_table().find(
        "Amoxycillin and Potassium Clavulanate", "500 mg + 125 mg"
    )
    assert in_force == corrected


def test_the_records_refuse_to_be_rewritten(store):
    store.save_equivalent(equivalent())
    for written in ("UPDATE formulation_equivalents SET molecule = 'x'",
                    "DELETE FROM formulation_equivalents"):  # fmt: skip
        with pytest.raises(sqlite3.IntegrityError, match="only ever added to"):
            store._connection.execute(written)


def test_a_database_with_nothing_recorded_finds_nothing(store):
    assert store.equivalents() == []
    assert len(store.equivalent_table()) == 0
