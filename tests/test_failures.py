import errno
import os
import sqlite3

import pytest

from fiber_bundle import database, objects
from fiber_bundle.store import Store


def only_receipt(config):
    connection = database.connect(config.state_root)
    try:
        rows = connection.execute("SELECT * FROM receipts").fetchall()
        assert len(rows) == 1
        return rows[0]
    finally:
        connection.close()


def test_disk_full_after_some_bytes_preserves_partial_and_resumes(
    store, config, request_data, monkeypatch
):
    real_write = objects._write_bytes
    writes = 0

    def fail(fd, data):
        nonlocal writes
        writes += 1
        if writes == 2:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data[:3])
                handle.flush()
                os.fsync(handle.fileno())
            raise OSError(errno.ENOSPC, "synthetic disk full")
        real_write(fd, data)

    with monkeypatch.context() as patch:
        patch.setattr(objects, "_write_bytes", fail)
        with pytest.raises(OSError) as error:
            store.ingest(**request_data)
        assert error.value.errno == errno.ENOSPC
    receipt = only_receipt(config)
    assert receipt["state"] == "failed" and receipt["error_code"] == "disk_full"
    assert list((config.state_root / "staging").glob("*.part"))
    assert Store(config).recover()[0].state == "incomplete"
    assert Store(config).ingest(**request_data).job_id == receipt["job_id"]
    assert Store(config).receipt(receipt["job_id"]).state == "stored"


def test_atomic_install_failure_preserves_staging(store, config, request_data, monkeypatch):
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise OSError(errno.EIO, "synthetic install failure")

        patch.setattr(objects.os, "link", fail)
        with pytest.raises(OSError):
            store.ingest(**request_data)
    assert only_receipt(config)["state"] == "failed"
    assert len(list((config.state_root / "staging").glob("*.part"))) == 1
    assert store.ingest(**request_data).state == "stored"


def test_database_transaction_failure_then_reconcile_ready_objects(store, config, request_data):
    connection = database.connect(config.state_root)
    connection.execute(
        "CREATE TRIGGER synthetic_fail BEFORE INSERT ON sources BEGIN "
        "SELECT RAISE(ABORT,'synthetic'); END"
    )
    connection.close()
    with pytest.raises(sqlite3.IntegrityError):
        store.ingest(**request_data)
    receipt = only_receipt(config)
    assert receipt["state"] == "failed" and receipt["source_version_id"] is None
    connection = database.connect(config.state_root)
    assert connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
    connection.execute("DROP TRIGGER synthetic_fail")
    connection.close()
    assert store.recover()[0].state == "stored"
    assert store.ingest(**request_data).job_id == receipt["job_id"]


def test_failure_status_write_failure_is_not_hidden(store, config, request_data, monkeypatch):
    connection = database.connect(config.state_root)
    connection.execute(
        "CREATE TRIGGER synthetic_fail BEFORE UPDATE ON receipts BEGIN "
        "SELECT RAISE(ABORT,'synthetic'); END"
    )
    connection.close()
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise OSError(errno.ENOSPC, "synthetic full")

        patch.setattr(objects, "put_object", fail)
        with pytest.raises(OSError) as error:
            store.ingest(**request_data)
    assert "failure status could not be persisted" in error.value.__notes__
    assert isinstance(error.value.__cause__, sqlite3.IntegrityError)
    assert only_receipt(config)["state"] == "receiving"


def test_receipt_insert_failure_leaves_no_false_ack_or_objects(store, config, request_data):
    connection = database.connect(config.state_root)
    connection.execute(
        "CREATE TRIGGER synthetic_fail BEFORE INSERT ON receipts BEGIN "
        "SELECT RAISE(ABORT,'synthetic'); END"
    )
    connection.close()
    with pytest.raises(sqlite3.IntegrityError):
        store.ingest(**request_data)
    connection = database.connect(config.state_root)
    assert connection.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0
    connection.close()
    assert not list((config.state_root / "objects").iterdir())
