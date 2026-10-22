import fcntl
import hashlib
import json
import os
import sqlite3
import sys

import pytest
from test_document_creation_config import creation_policy as creation_policy
from test_document_creation_execution import claim
from test_document_creation_execution import prepared_task as prepared_task
from test_document_creation_rounds import finish_rounds, output_file
from test_document_creation_tasks import input_task as input_task
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import auth, backend_lifecycle, backup_cli, backup_preflight, database, vaults
from fiber_bundle.backup_inventory import Limits
from fiber_bundle.config import IntegrityError, InvalidInput


@pytest.mark.parametrize("change", ["none", "missing", "bytes", "metadata"])
def test_document_draft_is_verified_after_native_owner_releases(
    config, owner, prepared_task, creation_policy, tmp_path, change
):
    from fiber_bundle import document_creation_artifacts as artifacts
    from fiber_bundle import document_creation_execution as execution
    from fiber_bundle import document_creation_profile as profile
    from fiber_bundle import document_creation_rounds as rounds
    from fiber_bundle.document_creation_config import parse

    creation_policy["model_config"].update(
        image=profile.IMAGE,
        max_calls=2,
        daily_calls=8,
        max_input_tokens=131072,
        max_request_bytes=131072,
        task_limit_nusd=100000000,
        daily_limit_nusd=200000000,
    )
    creation_policy["capability_revision"] = profile.REVISION
    policy = parse(creation_policy)
    with execution.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
        db = database.connect(config.state_root)
        with database.locked(config):
            attempt = claim(config, db, owner[0], prepared_task, policy, guard)
            rounds.attach(config, db, attempt, policy, guard)
        owned = (db, policy, guard, attempt)
        finish_rounds(config, owned)
        output_file(config, owned, tmp_path)
        artifacts.register(config, db, attempt, policy, guard, tmp_path)
        db.close()
    db = database.connect(config.state_root)
    path = (
        config.state_root
        / db.execute("SELECT relative_path FROM document_creation_artifacts").fetchone()[0]
    )
    if change == "metadata":
        db.execute("UPDATE document_creation_artifacts SET artifact_sha256=?", ("0" * 64,))
    db.close()
    if change == "missing":
        path.unlink()
    elif change == "bytes":
        path.write_bytes(b"changed")
    if change == "none":
        result = backup_preflight.inspect(config)
        assert result["document_drafts"] == 1
        assert {"path": "document-creation.sqlite3", "schema": 1} in result["databases"]
    else:
        with pytest.raises(IntegrityError, match="backup_draft_missing_or_changed"):
            backup_preflight.inspect(config)


def state_bytes(config):
    return {
        p.relative_to(config.state_root).as_posix(): p.read_bytes()
        for p in config.state_root.rglob("*")
        if p.is_file()
    }


def test_complete_observed_scope_without_mutation(config, store, request_data):
    store.ingest(**request_data)
    note = config.project_root / dict(config.vaults)["personal"] / "note.md"
    note.write_text("Synthetic private note")
    before = state_bytes(config)
    result = backup_preflight.inspect(config)
    assert result["state"] == "PASS"
    assert result["assets"] == {"retained": 3, "cleaned_after_publication": 0}
    assert result["receipts"] == {"stored": 1}
    assert result["databases"] == [{"path": "metadata.sqlite3", "schema": database.SCHEMA_VERSION}]
    assert any(
        e["path"] == "note.md" and e["sha256"] == hashlib.sha256(note.read_bytes()).hexdigest()
        for e in result["entries"]
    )
    assert not result["full_system_backup"] and not result["restorable"]
    assert "Synthetic private note" not in json.dumps(result)
    assert state_bytes(config) == before
    assert backup_preflight.recheck(config, result["sha256"]) == result


@pytest.mark.parametrize("change", ["edit", "add", "remove", "empty_directory", "metadata"])
def test_frozen_scope_rejects_later_change(config, store, change):
    folder = config.project_root / dict(config.vaults)["personal"]
    note = folder / "note.md"
    note.write_text("v1")
    result = backup_preflight.inspect(config)
    if change == "edit":
        note.write_text("v2")
    elif change == "add":
        (folder / "added.md").write_text("v1")
    elif change == "remove":
        note.unlink()
    elif change == "empty_directory":
        (folder / "empty").mkdir()
    else:
        db = database.connect(config.state_root)
        auth.create_client(db, "synthetic-backup", False)
        db.close()
    with pytest.raises(IntegrityError, match="backup_scope_changed"):
        backup_preflight.recheck(config, result["sha256"])


@pytest.mark.parametrize("change", ["edit", "new_file", "replace_directory"])
def test_external_edit_during_scan_rejects(config, store, monkeypatch, change):
    folder = config.project_root / dict(config.vaults)["personal"]
    note = folder / "note.md"
    note.write_text("v1")
    check = backup_preflight._objects

    def interleaved(db, inventory):
        if change == "edit":
            note.write_text("v2")
        elif change == "new_file":
            (folder / "added.md").write_text("v1")
        else:
            folder.rename(folder.with_name("old-folder"))
            folder.mkdir()
        return check(db, inventory)

    monkeypatch.setattr(backup_preflight, "_objects", interleaved)
    with pytest.raises((IntegrityError, FileNotFoundError)):
        backup_preflight.inspect(config)


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_changed_object_not_recovered(config, store, request_data, missing):
    store.ingest(**request_data)
    path = next((config.state_root / "objects").glob("*/*/*"))
    if missing:
        path.unlink()
    else:
        path.write_bytes(b"changed")
    before = state_bytes(config)
    with pytest.raises(IntegrityError, match="backup_object_missing_or_changed"):
        backup_preflight.inspect(config)
    assert state_bytes(config) == before


def test_cleaned_media_tombstone_is_preserved(config, store, request_data):
    store.ingest(**request_data)
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic-backup", False)
    row = db.execute("SELECT * FROM assets LIMIT 1").fetchone()
    db.execute(
        "INSERT INTO media_tasks VALUES ('job',?,?,?,'cancelled',NULL,NULL,1,1)",
        (client["id"], row["vault_id"], "{}"),
    )
    db.execute(
        "INSERT INTO media_cleanup VALUES ('job',?,'complete',1)",
        (json.dumps({"asset_id": row["id"], "source_object_deleted": 1}),),
    )
    db.close()
    (config.state_root / row["relative_path"]).unlink()
    result = backup_preflight.inspect(config)
    assert result["assets"] == {"retained": 2, "cleaned_after_publication": 1}


@pytest.mark.parametrize("name", [n for n in backup_preflight.DATABASES if n != "metadata.sqlite3"])
def test_sidecar_included_and_unsupported_version_refused(config, store, name):
    path = config.state_root / name
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE marker(value TEXT); PRAGMA user_version=1;")
    db.close()
    before = path.read_bytes()
    result = backup_preflight.inspect(config)
    assert {"path": name, "schema": 1} in result["databases"]
    assert path.read_bytes() == before
    db = sqlite3.connect(path)
    db.execute(
        "PRAGMA user_version=4" if name == "message-channels.sqlite3" else "PRAGMA user_version=2"
    )
    db.close()
    with pytest.raises(IntegrityError, match="backup_unsupported_schema"):
        backup_preflight.inspect(config)


def test_channel_sidecar_v3_schema_is_recorded_without_mutation(config, store):
    from fiber_bundle.channels import store as channel_store

    with channel_store.opened(config):
        pass
    path = config.state_root / "message-channels.sqlite3"
    before = path.read_bytes()
    result = backup_preflight.inspect(config)
    assert {"path": path.name, "schema": 3} in result["databases"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("fault", ["schema", "foreign_key", "broken"])
def test_database_failures_do_not_migrate_or_repair(config, store, fault):
    path = config.state_root / "metadata.sqlite3"
    if fault == "broken":
        path.write_bytes(b"not sqlite")
    else:
        db = sqlite3.connect(path)
        if fault == "schema":
            db.execute("PRAGMA user_version=12")
        else:
            db.execute("INSERT INTO sources VALUES ('s','missing','source','now')")
        db.commit()
        db.close()
    before = path.read_bytes()
    with pytest.raises((IntegrityError, sqlite3.Error)):
        backup_preflight.inspect(config)
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ["metadata.sqlite3-wal", "weixin-inbox.sqlite3-journal"])
def test_hot_journal_or_wal_requires_offline_checkpoint(config, store, name):
    (config.state_root / name).write_bytes(b"journal")
    with pytest.raises(InvalidInput, match="backup_database_requires_offline_checkpoint"):
        backup_preflight.inspect(config)


def test_zero_length_journal_included(config, store):
    (config.state_root / "metadata.sqlite3-wal").touch()
    result = backup_preflight.inspect(config)
    assert any(e["path"] == "metadata.sqlite3-wal" for e in result["entries"])


@pytest.mark.parametrize("owner", ["storage", "backend", "execution", "ticket"])
def test_live_owner_refused_without_stopping_or_cleaning(config, store, owner):
    if owner == "storage":
        path = config.state_root / "storage.lock"
    elif owner == "backend":
        path = config.state_root / "backend.lock"
    elif owner == "execution":
        folder = config.state_root / "execution-locks"
        folder.mkdir()
        path = folder / "synthetic-native-owner"
    else:
        folder = config.state_root / "execution-queue"
        folder.mkdir()
        path = folder / "synthetic.ticket"
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        before = state_bytes(config)
        with pytest.raises(InvalidInput, match="backup_(runtime|storage)_busy"):
            backup_preflight.inspect(config)
        assert state_bytes(config) == before
    assert backup_preflight.inspect(config)["state"] == "PASS"


def test_stopped_backend_record_is_inventory_only(config, store):
    control = backend_lifecycle.BackendControl(config, "http://127.0.0.1:8765")
    control.record("stopped")
    assert backup_preflight.inspect(config)["runtime_policy"] == "no_start_no_replay_no_migration"


@pytest.mark.parametrize("change", ["new_owner", "replaced_lock"])
def test_new_or_replaced_native_lock_during_preflight_refused(config, store, monkeypatch, change):
    check = backup_preflight._state_scope
    calls = 0

    def changed(config):
        nonlocal calls
        calls += 1
        # Between acquisition of native locks and collection of the file inventory.
        if calls == 2:
            path = config.state_root / ("backend.lock" if change == "new_owner" else "storage.lock")
            if change == "replaced_lock":
                path.unlink()
            path.touch()
        check(config)

    monkeypatch.setattr(backup_preflight, "_state_scope", changed)
    with pytest.raises(IntegrityError, match="backup_runtime_changed"):
        backup_preflight.inspect(config)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_vault_files_never_followed(config, store, tmp_path, kind):
    source = tmp_path / "private-sentinel"
    source.write_bytes(b"do not follow")
    path = config.project_root / dict(config.vaults)["personal"] / "unsafe"
    if kind == "symlink":
        path.symlink_to(source)
    elif kind == "hardlink":
        os.link(source, path)
    else:
        os.mkfifo(path)
    with pytest.raises(InvalidInput):
        backup_preflight.inspect(config)
    assert source.read_bytes() == b"do not follow"


def test_browser_profile_not_traversed_and_unknown_state_refused(config, store, tmp_path):
    profile = config.state_root / "browser-profiles"
    profile.mkdir()
    (profile / "dont-follow").symlink_to(tmp_path)
    result = backup_preflight.inspect(config)
    assert not any(e["path"].startswith("browser-profiles") for e in result["entries"])
    (config.state_root / "unrecognized-home").mkdir()
    with pytest.raises(InvalidInput, match="backup_unclassified_state"):
        backup_preflight.inspect(config)


def test_registered_pending_vault_included_and_identity_change_refused(config, store):
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic-backup", True)
    identity = vaults.reserve(config, db, client["id"], "pending-test")
    row = db.execute("SELECT * FROM vaults WHERE id=?", (identity,)).fetchone()
    db.close()
    result = backup_preflight.inspect(config)
    assert any(v["id"] == identity and v["status"] == "pending" for v in result["vaults"])
    folder = config.project_root / row["relative_path"]
    folder.rename(folder.with_name("old-created"))
    folder.mkdir()
    with pytest.raises(IntegrityError, match="vault directory identity changed"):
        backup_preflight.inspect(config)


@pytest.mark.parametrize(
    "limits",
    [
        Limits(entries=2),
        Limits(file_bytes=1),
        Limits(total_bytes=1),
        Limits(depth=1),
        Limits(entries=0),
    ],
)
def test_limits_are_shared_and_fail_closed(config, store, limits):
    folder = config.project_root / dict(config.vaults)["personal"] / "one" / "two"
    folder.mkdir(parents=True)
    (folder / "note").write_text("synthetic")
    with pytest.raises(InvalidInput):
        backup_preflight.inspect(config, limits=limits)


def test_cli_private_report_bounded_output_and_no_overwrite(
    config, store, tmp_path, monkeypatch, capsys
):
    report = tmp_path / "manifest.json"
    args = [
        "fiber-backup",
        "preflight",
        "--project-root",
        str(config.project_root),
        "--config",
        "config.json",
        "--report",
        str(report),
    ]
    monkeypatch.setattr(sys, "argv", args)
    assert backup_cli.main() == 0
    output = json.loads(capsys.readouterr().out)
    saved = report.read_bytes()
    assert not output["restorable"] and "entries" in output
    assert report.stat().st_mode & 0o777 == 0o600
    assert "storage_config" not in output
    assert backup_cli.main() == 1
    assert report.read_bytes() == saved
    assert "FileExistsError" in capsys.readouterr().out


def test_cli_report_cannot_write_into_scope(config, store, monkeypatch, capsys):
    path = config.state_root / "new.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fiber-backup",
            "preflight",
            "--project-root",
            str(config.project_root),
            "--config",
            "config.json",
            "--report",
            str(path),
        ],
    )
    assert backup_cli.main() == 1
    assert not path.exists()
    assert "InvalidInput" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["x", "A" * 64, None, 123])
def test_invalid_digest_refused(config, store, value):
    with pytest.raises(InvalidInput, match="backup_digest_invalid"):
        backup_preflight.recheck(config, value)


def test_primary_schema_is_not_treated_as_a_version_one_sidecar(config, store):
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        db.execute("PRAGMA user_version=1")
    with pytest.raises(IntegrityError, match="backup_unsupported_schema"):
        backup_preflight.inspect(config)


def test_durable_organization_pause_is_in_backup_scope(config, store):
    from uuid import uuid4

    from fiber_bundle.organization import execution_control

    job = {"id": str(uuid4()), "generation": 3}
    execution_control.request(config, job)
    result = backup_preflight.inspect(config)
    assert result["state"] == "PASS"
    assert any(
        e["path"] == f"organization-execution/{job['id']}.pause.json" for e in result["entries"]
    )
    assert execution_control.requested(config, job)
