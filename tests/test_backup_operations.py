import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_document_creation_execution import prepared_task as prepared_task
from test_document_creation_tasks import input_task as input_task
from test_execution import bound as bound
from test_execution import policy as policy
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_moc_associations import ledger, link, run
from test_moc_associations import moc_scope as moc_scope
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_query_jobs import job as job
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import (
    backup_bundle,
    backup_preflight,
    backup_requests,
    backup_restore,
    cloud_files,
    database,
)
from fiber_bundle.config import IntegrityError, InvalidInput, load_config

PASSWORD = "synthetic-backup-password-only"


def test_pending_query_and_unknown_fee_never_resume(config, job, bound, tmp_path):
    db = database.connect(config.state_root)
    run_id = bound[0]
    db.execute(
        "INSERT INTO model_calls VALUES ('unknown',?,'model','synthetic','2026-10-06','USD',"
        "100,NULL,NULL,NULL,'uncertain',0)",
        (run_id,),
    )
    fees = [tuple(r) for r in db.execute("SELECT * FROM model_calls")]
    db.close()
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    target = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    backup_restore.activate(target, source_stopped=True)
    db = database.connect(target.state_root)
    assert [tuple(r) for r in db.execute("SELECT * FROM model_calls")] == fees
    assert (
        db.execute("SELECT state FROM query_jobs WHERE id=?", (job["id"],)).fetchone()[0]
        == "cancelled"
    )
    assert (
        db.execute("SELECT state FROM execution_runs WHERE id=?", (run_id,)).fetchone()[0]
        == "interrupted"
    )
    db.close()


def test_moc_membership_history_and_rebackup(config, moc_scope, tmp_path):
    run(config, moc_scope)
    link(config, moc_scope, owner="manual")
    before = ledger(config)
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    target = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    after = ledger(target)
    left, right = json.loads(before[0][0][3]), json.loads(after[0][0][3])
    assert left["members"] == right["members"] and before[1] == after[1]
    assert right["vault_ino"] == target.vault_path(dict(target.vaults)["personal"]).stat().st_ino
    backup_bundle.create(target, tmp_path / "bundle2", PASSWORD)
    backup_restore.restore(tmp_path / "bundle2", tmp_path / "restored2", PASSWORD)
    assert list((tmp_path / "restored2/.state/snapshots").glob("restore-history-*.json"))


def test_admin_only_prepares_no_password_or_data_copy(config, http, tmp_path):
    result = http.post("/admin/backups", json={"action": "create"})
    assert result.status_code == 201
    value = result.json()
    assert value["state"] == "prepared"
    assert not Path(value["destination"]).exists()
    assert not (config.project_root / "backups").exists()
    assert http.get("/admin/backups").json() == [value]
    assert (
        http.post("/admin/backups", json={"action": "create", "password": PASSWORD}).status_code
        == 422
    )
    old = http.headers.pop("X-CSRF-Token")
    assert http.post("/admin/backups", json={"action": "create"}).status_code == 403
    http.headers["X-CSRF-Token"] = old
    assert (
        http.post("/admin/backups", json={"action": "restore", "bundle": value["id"]}).status_code
        == 503
    )
    http.cookies.clear()
    assert http.get("/admin/backups").status_code == 401


def test_request_execute_restore_and_binding(config, store, tmp_path):
    intent = backup_requests.prepare(config, "create")
    backup_requests.execute(config, intent["id"], PASSWORD)
    assert backup_requests.list_requests(config)[0]["state"] == "encrypted_complete"
    restore = backup_requests.prepare(config, "restore", intent["id"])
    result = backup_requests.execute(config, restore["id"], PASSWORD)
    assert result["state"] == "restored_locked"
    with pytest.raises(InvalidInput):
        backup_requests.execute(config, intent["id"], PASSWORD)
    with pytest.raises(InvalidInput, match="configuration_changed"):
        backup_requests.read(replace(config, state_relative=".different"), intent["id"])
    for value in ["../bad", "not-uuid", "A" * 36]:
        with pytest.raises(InvalidInput):
            backup_requests.read(config, value)
    assert (
        PASSWORD
        not in (config.project_root / ".backup-requests" / (intent["id"] + ".json")).read_text()
    )


def test_external_roots_reject_overlap_and_replaced_symlink(config, store, tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    mapped = replace(config, vault_roots=(("personal", str(external)),))
    mapped.validate()
    external.rmdir()
    external.symlink_to(config.state_root, target_is_directory=True)
    with pytest.raises(InvalidInput):
        mapped.vault_path(dict(mapped.vaults)["personal"])
    with pytest.raises(InvalidInput):
        replace(config, vault_roots=(("personal", str(config.state_root)),)).validate()


def test_placeholder_preflight_does_not_open_content(config, store):
    placeholder = config.vault_path(dict(config.vaults)["personal"]) / ".note.md.icloud"
    placeholder.write_bytes(b"not-materialized")
    with pytest.raises(IntegrityError, match="not_downloaded"):
        backup_preflight.inspect(config)


def test_dataless_ancestor_denied_before_child_stat(tmp_path, monkeypatch):
    base = Path.lstat
    calls = []
    root = tmp_path / "pending"
    root.mkdir()

    def fake(self, *args, **kwargs):
        calls.append(self)
        info = base(self, *args, **kwargs)
        if self == root:
            return SimpleNamespace(st_flags=cloud_files.SF_DATALESS, st_mode=info.st_mode)
        return info

    monkeypatch.setattr(Path, "lstat", fake)
    with pytest.raises(IntegrityError):
        cloud_files.require_local(root / "child")
    assert root / "child" not in calls


@pytest.mark.parametrize("status", ["pending", "stale", "conflict", "broken"])
def test_native_cloud_status_fails_closed(tmp_path, monkeypatch, status):
    root = tmp_path / "Mobile Documents"
    root.mkdir()
    helper = tmp_path / "helper"
    helper.write_bytes(b"synthetic-helper")
    helper.chmod(0o700)
    import hashlib

    monkeypatch.setenv("FIBER_CLOUD_HELPER", str(helper))
    monkeypatch.setenv("FIBER_CLOUD_HELPER_SHA256", hashlib.sha256(helper.read_bytes()).hexdigest())
    stdout = (
        b"broken"
        if status == "broken"
        else json.dumps({"schema": "cloud-status/v1", "available": False, status: 1}).encode()
    )
    monkeypatch.setattr(
        cloud_files.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=stdout)
    )
    with pytest.raises(IntegrityError, match="pending_stale_or_conflicted"):
        cloud_files.require_available(root, tree=True)
    helper.write_bytes(b"changed")
    with pytest.raises(IntegrityError, match="helper_changed"):
        cloud_files.require_available(root)
    monkeypatch.delenv("FIBER_CLOUD_HELPER")
    with pytest.raises(InvalidInput, match="helper_required"):
        cloud_files.require_available(root)


@pytest.mark.parametrize("replacement", [False, True])
def test_absent_registered_original_retains_identity_and_requires_explicit_rebind(
    config, identity_scope, tmp_path, replacement
):
    from test_note_identities import track

    from fiber_bundle import note_identities, note_identity_store, note_index

    tracked = track(config, identity_scope)
    path = identity_scope[2] / "manual.md"
    path.unlink()
    if replacement:
        path.write_text("# A different original")
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    target = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    records = note_identity_store.records(target, identity_scope[1])
    assert records[0].identity == tracked["note_identity"]
    root = target.vault_path(dict(target.vaults)["personal"])
    notes = [note_index.read_note(root, "manual.md", 1024)] if replacement else []
    key = (root.stat().st_dev, root.stat().st_ino)
    if replacement:
        with pytest.raises(IntegrityError, match="^layout_tracked_note_replaced$"):
            note_identities.resolve(notes, records, key)
    else:
        assert note_identities.resolve(notes, records, key) == []


def test_setup_fresh_local_and_no_existing_adoption(tmp_path, monkeypatch):
    from fiber_bundle import setup_cli

    root = tmp_path / "fresh"
    setup_cli.initialize(root, {})
    config = load_config(root / "config.test.json", root)
    db = database.connect(config.state_root)
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    assert db.execute("SELECT count(*) FROM admin_auth").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()
    before = (root / "config.test.json").read_bytes()
    with pytest.raises(InvalidInput):
        setup_cli.initialize(root, {})
    assert (root / "config.test.json").read_bytes() == before
    assert setup_cli.inspect()["services_started"] is False


def test_sidecar_admissions_and_outbound_intents_quarantined(config, prepared_task, tmp_path):
    import sqlite3

    from fiber_bundle import document_creation_ledger, moc_dispatch_store

    with moc_dispatch_store.connection(config, write=True) as side:
        side.execute("INSERT INTO jobs VALUES('job','client','vault','{}',NULL,'queued')")
    journal = config.state_root / "weixin-inbox.sqlite3"
    with sqlite3.connect(journal) as side:
        side.execute("CREATE TABLE wx_batches(id TEXT PRIMARY KEY,state TEXT)")
        side.execute("INSERT INTO wx_batches VALUES('batch','frozen')")
        side.execute("CREATE TABLE wx_task_deliveries(id TEXT PRIMARY KEY,state TEXT)")
        side.executemany(
            "INSERT INTO wx_task_deliveries VALUES(?,?)",
            [("pending", "pending"), ("sending", "sending"), ("sent", "sent")],
        )
        side.execute("PRAGMA user_version=1")
    journal.chmod(0o600)
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    target = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    with sqlite3.connect(target.state_root / "weixin-inbox.sqlite3") as side:
        assert side.execute("SELECT state FROM wx_batches").fetchone()[0] == "cancelled"
        assert dict(side.execute("SELECT id,state FROM wx_task_deliveries")) == {
            "pending": "unknown",
            "sending": "unknown",
            "sent": "sent",
        }
    with document_creation_ledger.connection(target) as side:
        assert side.execute("SELECT state FROM tasks").fetchone()[0] == "cancelled"
    with moc_dispatch_store.connection(target) as side:
        assert side.execute("SELECT state FROM jobs").fetchone()[0] == "failed"


def test_backend_child_environment_uses_portable_runtime_without_user_site(monkeypatch):
    from fiber_bundle.backend_cli import environment

    monkeypatch.setenv("PYTHONHOME", "/synthetic/portable/python")
    monkeypatch.setenv("FIBER_CLOUD_HELPER", "/synthetic/cloud-status")
    monkeypatch.setenv("FIBER_CLOUD_HELPER_SHA256", "a" * 64)
    monkeypatch.setenv("UNRELATED_PRIVATE_ENV", "private-env-sentinel")
    value = environment()
    assert value["PYTHONHOME"] == "/synthetic/portable/python"
    assert value["PYTHONNOUSERSITE"] == "1"
    assert value["PYTHONDONTWRITEBYTECODE"] == "1"
    assert value["FIBER_CLOUD_HELPER_SHA256"] == "a" * 64
    assert "UNRELATED_PRIVATE_ENV" not in value


def test_config_checks_cloud_ancestor_before_resolving_external_root(
    config, store, tmp_path, monkeypatch
):
    external = tmp_path / "pending"
    external.mkdir()
    original = Path.is_dir

    def no_directory_read(self):
        if self == external:
            raise AssertionError("external stat before cloud check")
        return original(self)

    monkeypatch.setattr(Path, "is_dir", no_directory_read)

    def denied(path):
        assert path == external
        raise IntegrityError("cloud_item_not_downloaded")

    monkeypatch.setattr(cloud_files, "require_local", denied)
    with pytest.raises(InvalidInput, match="unavailable"):
        replace(config, vault_roots=(("personal", str(external)),)).validate()
