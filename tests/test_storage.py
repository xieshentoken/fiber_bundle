import json
import sqlite3
from dataclasses import replace

import pytest

from fiber_bundle import database, objects
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.inputs import AssetInput
from fiber_bundle.store import EventConflict, Store


def rows(config, sql, args=()):
    connection = database.connect(config.state_root)
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


def test_roundtrip_reopen_and_idempotence(store, config, request_data):
    result = store.ingest(**request_data)
    assert result.state == "stored"
    assert Store(config).ingest(**request_data) == result
    assert rows(config, "SELECT count(*) FROM receipts")[0][0] == 1
    assets = rows(config, "SELECT * FROM assets")
    assert len(assets) == 3
    values = [Store(config).read_asset(row["id"], vault="personal") for row in assets]
    assert request_data["raw_event"] in values
    assert bytes(range(256)) in values
    assert "原文一".encode() in values
    assert Store(config).recover() == [result]


def test_same_content_new_event_is_new_task_and_preserves_both_events(store, config, request_data):
    first = store.ingest(**request_data)
    second = store.ingest(**{**request_data, "event_id": "event-2", "raw_event": b'"event-2"'})
    assert first.job_id != second.job_id
    assert first.source_version_id == second.source_version_id
    assert rows(config, "SELECT count(*) FROM receipts")[0][0] == 2
    assert rows(config, "SELECT count(*) FROM assets")[0][0] == 4
    for row in rows(config, "SELECT raw_asset_id FROM receipts"):
        assert store.read_asset(row[0], vault="personal") in {
            request_data["raw_event"],
            b'"event-2"',
        }


def test_reusing_event_id_with_changed_input_is_conflict(store, config, request_data):
    original = store.ingest(**request_data)
    with pytest.raises(EventConflict):
        store.ingest(**{**request_data, "raw_event": b"different"})
    assert store.receipt(original.job_id) == original
    assert rows(config, "SELECT count(*) FROM receipts")[0][0] == 1


def test_versions_are_immutable_and_reverted_content_gets_new_version(store, config, request_data):
    first = store.ingest(**request_data)
    old = [tuple(row) for row in rows(config, "SELECT * FROM version_assets")]
    changed = {
        **request_data,
        "event_id": "event-2",
        "items": (
            AssetInput(b"changed", "body.txt", "text/plain", "body"),
            request_data["items"][1],
        ),
    }
    second = store.ingest(**changed)
    third = store.ingest(**{**request_data, "event_id": "event-3"})
    assert len({r.source_version_id for r in (first, second, third)}) == 3
    assert [
        row[0] for row in rows(config, "SELECT version_no FROM source_versions ORDER BY version_no")
    ] == [1, 2, 3]
    assert [
        tuple(row)
        for row in rows(
            config, "SELECT * FROM version_assets WHERE version_id=?", (first.source_version_id,)
        )
    ] == old


def test_cross_vault_and_ingress_scopes_are_independent(store, config, request_data):
    first = store.ingest(**request_data)
    other = store.ingest(**{**request_data, "ingress_scope": "another", "vault": "supplier"})
    assert first.source_version_id != other.source_version_id
    assets = rows(
        config, "SELECT a.id FROM assets a JOIN vaults v ON v.id=a.vault_id WHERE v.name='personal'"
    )
    with pytest.raises(InvalidInput):
        store.read_asset(assets[0][0], vault="supplier")
    assert rows(config, "SELECT count(*) FROM assets")[0][0] == 6


def test_clear_one_session_keeps_knowledge_other_references_and_replay_does_not_restore(
    store, config, request_data
):
    first = store.ingest(**request_data)
    store.ingest(**{**request_data, "event_id": "event-2", "session_id": "session-2"})
    assert store.clear_session_refs("synthetic:owner", "session-1") == 1
    assert store.clear_session_refs("another", "session-2") == 0
    assert store.ingest(**request_data) == first
    store.recover()
    assert [row[0] for row in rows(config, "SELECT session_id FROM session_refs")] == ["session-2"]
    assert rows(config, "SELECT retention FROM source_versions")[0][0] == "knowledge"
    for row in rows(config, "SELECT * FROM assets"):
        assert objects.digest(store.read_asset(row["id"], vault="personal")) == row["sha256"]


def test_retention_only_promotes(store, config, request_data):
    store.ingest(**{**request_data, "retention": "session"})
    store.ingest(**{**request_data, "event_id": "event-2", "retention": "knowledge"})
    store.ingest(**{**request_data, "event_id": "event-3", "retention": "session"})
    assert rows(config, "SELECT retention FROM source_versions")[0][0] == "knowledge"


def test_missing_bytes_resume_same_receipt(store, config, request_data):
    original = request_data["items"][1]
    missing = replace(
        original,
        data=None,
        expected_sha256=objects.digest(original.data),
        expected_size=len(original.data),
    )
    incomplete = store.ingest(**{**request_data, "items": (request_data["items"][0], missing)})
    assert incomplete.state == "incomplete"
    assert incomplete.source_version_id is None
    assert store.recover()[0].state == "incomplete"
    with pytest.raises(IntegrityError):
        store.snapshot()
    complete = store.ingest(**request_data)
    assert complete.job_id == incomplete.job_id and complete.state == "stored"
    assert rows(config, "SELECT count(*) FROM receipts")[0][0] == 1


def test_corrupt_or_missing_object_never_reports_stored(store, config, request_data):
    result = store.ingest(**request_data)
    asset = rows(config, "SELECT * FROM assets")[0]
    path = config.state_root / asset["relative_path"]
    original = path.read_bytes()
    path.write_bytes(b"corrupt")
    with pytest.raises(IntegrityError):
        store.read_asset(asset["id"], vault="personal")
    assert store.recover()[0].state == "failed"
    path.unlink()
    assert store.recover()[0].state == "incomplete"
    assert store.ingest(**request_data).job_id == result.job_id
    assert path.read_bytes() == original


def test_database_manifest_and_path_tampering_are_rejected(store, config, request_data):
    result = store.ingest(**request_data)
    connection = database.connect(config.state_root)
    try:
        connection.execute("UPDATE receipts SET manifest_json='{}'")
    finally:
        connection.close()
    with pytest.raises(IntegrityError):
        store.receipt(result.job_id)
    assert store.recover()[0].state == "failed"
    connection = database.connect(config.state_root)
    try:
        row = connection.execute("SELECT id FROM assets LIMIT 1").fetchone()
        connection.execute("UPDATE assets SET relative_path='../outside' WHERE id=?", (row[0],))
    finally:
        connection.close()
    with pytest.raises(IntegrityError):
        store.read_asset(row[0], vault="personal")


def test_metadata_snapshot_is_consistent_and_objects_match(store, config, request_data):
    store.ingest(**request_data)
    snapshot = store.snapshot()
    connection = sqlite3.connect(snapshot)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            connection.execute("SELECT count(*) FROM receipts WHERE state='stored'").fetchone()[0]
            == 1
        )
    finally:
        connection.close()
    listing = json.loads(snapshot.with_suffix(".sqlite3.json").read_text())
    assert listing["database_sha256"] == objects.digest(snapshot.read_bytes())
    assert len(listing["assets"]) == 3
    for asset in listing["assets"]:
        content = (config.state_root / asset["relative_path"]).read_bytes()
        assert len(content) == asset["byte_count"] and objects.digest(content) == asset["sha256"]


@pytest.mark.parametrize(
    "changes",
    [
        {"event_id": ""},
        {"ingress_scope": 1},
        {"vault": "real-vault"},
        {"items": ()},
        {"items": []},
        {"raw_event": "not bytes"},
        {"retention": "published"},
        {"items": (AssetInput(b"x", "../escape", "text/plain"),)},
        {"items": (AssetInput(b"x", "x", "not-a-mime"),)},
        {"items": (AssetInput(None, "missing", "text/plain"),)},
        {"items": (AssetInput(b"x", "x", "text/plain", expected_sha256="0" * 64),)},
        {"items": (AssetInput(b"x", "x", "text/plain", expected_size=True),)},
    ],
)
def test_invalid_input_has_no_receipt(store, config, request_data, changes):
    with pytest.raises(InvalidInput):
        store.ingest(**{**request_data, **changes})
    assert rows(config, "SELECT count(*) FROM receipts")[0][0] == 0
