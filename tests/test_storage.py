"""Tests for SqliteStore: protocol conformance plus the actual durability
claim - that a second, independent SqliteStore instance pointed at the same
file sees everything a previous instance wrote, even after that previous
instance (and every Python object it held) is gone.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from backend.models import Job, JobStatus
from backend.dal import SqliteStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "store.db"


def test_get_missing_key_returns_none(db_path):
    store = SqliteStore(db_path)
    assert store.get("does-not-exist") is None
    store.close()


def test_put_and_get_roundtrip(db_path):
    store = SqliteStore(db_path)
    job = Job(id="1", qasm="OPENQASM 2.0; ...", num_shots=100, status=JobStatus.DONE, counts={"00": 100})

    store.put("1", job)

    assert store.get("1") == job
    store.close()


def test_update_missing_key_raises(db_path):
    store = SqliteStore(db_path)
    with pytest.raises(KeyError):
        store.update("does-not-exist", lambda job: job)
    store.close()


def test_update_applies_mutation_and_persists_it(db_path):
    store = SqliteStore(db_path)
    store.put("1", Job(id="1", qasm="qasm", num_shots=10, status=JobStatus.QUEUED))

    updated = store.update("1", lambda job: replace(job, status=JobStatus.RUNNING))

    assert updated.status == JobStatus.RUNNING

    stored_job = store.get("1")
    assert stored_job is not None and stored_job.status == JobStatus.RUNNING
    store.close()


def test_data_survives_reopening_the_file(db_path):
    """The actual durability claim: build a store, write to it, close it -
    then build a brand new SqliteStore pointed at the same file and confirm
    it sees the same data. This is what "survives a restart" means in
    practice, since a restarted process would do exactly this: construct a
    fresh SqliteStore around the same path.
    """
    store = SqliteStore(db_path)
    job = Job(id="1", qasm="qasm", num_shots=10, status=JobStatus.DONE, counts={"00": 4, "11": 6})
    store.put("1", job)
    store.close()

    reopened = SqliteStore(db_path)
    try:
        assert reopened.get("1") == job
    finally:
        reopened.close()
