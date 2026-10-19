"""Isolated behavior tests; never read/write a configured real Vault or Keychain."""

import json
import sqlite3
from pathlib import Path

import pytest

from fiber_bundle import backup_bundle, backup_restore, database
from fiber_bundle.backup_automation import runner, target, verification
from fiber_bundle.backup_automation import store as backups
from fiber_bundle.backup_automation.policy import Policy, Save
from fiber_bundle.config import IntegrityError, InvalidInput, load_config

KEY = "a1" * 32


@pytest.fixture
def configured(config, store, tmp_path, tmp_path_factory, monkeypatch):
    from fiber_bundle.model_configuration import keychain

    secrets = {}
    monkeypatch.setattr(keychain, "put", lambda account, value: secrets.update({account: value}))
    monkeypatch.setattr(keychain, "get", lambda account: secrets[account])
    folder = tmp_path_factory.mktemp("synthetic-encrypted-backups")
    save = Save(
        revision=0,
        policy=Policy(directory=str(folder)),
        storage_mode="complete",
        recovery_key=KEY,
        key_saved=True,
    )
    backups.save(config, save)
    return folder


def test_disabled_install_and_auth(config, http):
    assert not (config.project_root / ".backup-automation").exists()
    response = http.get("/admin/backup-automation")
    assert response.status_code == 200
    value = response.json()
    assert not value["policy"]["enabled"] and not value["key_configured"]
    assert not (config.project_root / ".backup-automation").exists()
    assert http.post("/admin/backup-automation/jobs").status_code == 422
    http.headers.pop("X-CSRF-Token")
    assert (
        http.put("/admin/backup-automation", json={"revision": 0, "policy": {}}).status_code == 403
    )
    http.cookies.clear()
    assert http.get("/admin/backup-automation").status_code == 401


def test_roundtrip_preserves_dedup_and_notes(config, configured, request_data):
    from fiber_bundle.store import Store

    source = Store(config)
    source.ingest(**request_data)
    root = config.vault_path(dict(config.vaults)["personal"])
    note = root / "sample.md"
    note.write_text("# Synthetic note\nBody and ![[images/chart.png]]")
    (root / "images").mkdir()
    (root / "images/chart.png").write_bytes(b"synthetic-image")
    original = note.read_bytes()
    job = backups.enqueue(config, "manual")
    assert runner.execute(config)
    done = backups.status(config)["jobs"][0]
    assert done["id"] == job["id"] and done["state"] == "succeeded", done
    assert done["authenticated"] and done["pinned"]
    bundle = Path(done["destination"])
    verification.verify(bundle, KEY)
    backup_restore.restore(bundle, config.project_root / "clean-restoration", KEY)
    restored = load_config(
        config.project_root / "clean-restoration/config.test.json",
        config.project_root / "clean-restoration",
    )
    assert (
        restored.vault_path(dict(restored.vaults)["personal"]) / "sample.md"
    ).read_bytes() == original
    with (
        database.connect(config.state_root) as left,
        sqlite3.connect(restored.state_root / "metadata.sqlite3") as right,
    ):
        assert [tuple(r) for r in left.execute("SELECT * FROM receipts")] == [
            tuple(r) for r in right.execute("SELECT * FROM receipts")
        ]
    assert note.read_bytes() == original
    ledger = (backups.directory(config) / "state.json").read_text()
    assert KEY not in ledger and KEY not in json.dumps(backups.status(config))


def test_online_wal_and_offline_compatibility(config, store, tmp_path):
    path = config.state_root / "metadata.sqlite3"
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE backup_synthetic (id TEXT)")
    writer.execute("INSERT INTO backup_synthetic VALUES ('committed')")
    backup_bundle.create(config, tmp_path / "online", KEY, online=True)
    verification.verify(tmp_path / "online", KEY)
    writer.close()
    backup_restore.restore(tmp_path / "online", tmp_path / "restored", KEY)
    with sqlite3.connect(tmp_path / "restored/.state/metadata.sqlite3") as db:
        assert db.execute("SELECT * FROM backup_synthetic").fetchall() == [("committed",)]


def test_failure_keeps_baseline_and_retry_is_new_archive(config, configured, monkeypatch):
    backups.enqueue(config, "manual")

    def fail(*args, **kwargs):
        raise InvalidInput("backup_source_changed")

    create = backup_bundle.create
    monkeypatch.setattr(backup_bundle, "create", fail)
    runner.execute(config)
    value = backups.read(config)
    assert value["jobs"][0]["state"] == "failed" and value["baseline"] is None
    first = value["jobs"][0]["destination"]
    monkeypatch.setattr(backup_bundle, "create", create)
    backups.enqueue(config, "manual")
    runner.execute(config)
    done = backups.status(config)["jobs"][0]
    assert done["state"] == "succeeded" and done["destination"] != first
    assert len(backups.status(config)["jobs"]) == 2


def test_targets_fail_closed(config, configured, tmp_path):
    for path in (
        config.state_root,
        config.project_root,
        config.vault_path(dict(config.vaults)["personal"]),
    ):
        with pytest.raises(InvalidInput, match="overlaps_source"):
            target.identity(config, str(path))
    value = target.identity(config, str(configured))
    configured.rename(tmp_path / "old-disk")
    configured.mkdir()
    with pytest.raises(InvalidInput, match="identity_changed"):
        target.checked(config, value)


def test_tamper_is_not_accepted(config, configured):
    backups.enqueue(config, "manual")
    runner.execute(config)
    path = Path(backups.status(config)["jobs"][0]["destination"])
    file = path / "state.fbenc"
    data = bytearray(file.read_bytes())
    data[-5] ^= 1
    file.write_bytes(data)
    with pytest.raises(IntegrityError):
        verification.verify(path, KEY)


def test_interruption_and_deduplicated_queue(config, configured):
    first = backups.enqueue(config, "manual")
    assert backups.enqueue(config, "manual")["id"] == first["id"]
    with backups.edit(config) as state:
        state["jobs"][0]["state"] = "running"
    runner.recover(config)
    assert backups.status(config)["jobs"][0]["error"] == "backup_interrupted"
    assert not runner.execute(config)


# Automatic policies use a deterministic clock and small isolated source mutations.
def enable(config, **settings):
    data = backups.status(config)
    policy = Policy(**{**data["policy"], "enabled": True, **settings})
    backups.save(config, Save(revision=data["revision"], policy=policy))


def test_all_four_triggers_and_missed_intervals(config, configured):
    import time

    from fiber_bundle.backup_automation import events, scheduler

    now = time.time()
    enable(config, interval_minutes=1)
    scheduler.tick(config, now=now + 61)
    assert backups.status(config)["jobs"][0]["reason"] == "interval"
    assert runner.execute(config)
    # Nothing changed: even a very late wakeup cannot generate a catch-up storm.
    scheduler.tick(config, now=now + 360000)
    assert len(backups.status(config)["jobs"]) == 1
    enable(config, interval_minutes=0, on_change=True, debounce_seconds=30)
    root = config.vault_path(dict(config.vaults)["personal"])
    (root / "new.md").write_text("new business note")
    scheduler.tick(config, now=now + 400000)
    assert len(backups.status(config)["jobs"]) == 1
    scheduler.tick(config, now=now + 400031)
    assert backups.status(config)["jobs"][0]["reason"] == "business_change"
    runner.execute(config)
    enable(config, on_change=False, growth_mb=1)
    (root / "large.md").write_text("a" * (1024**2 + 1))
    scheduler.tick(config, now=now + 500000)
    assert backups.status(config)["jobs"][0]["reason"] == "data_growth"
    runner.execute(config)
    enable(config, growth_mb=0, after_moc=True)
    events.completed(config, "synthetic-batch", {"members": 3})
    events.completed(config, "synthetic-batch", {"members": 3})
    assert len(backups.read(config)["pending_moc"]) == 1
    scheduler.tick(config, now=now + 600000)
    assert backups.status(config)["jobs"][0]["reason"] == "moc_batch"
    runner.execute(config)
    assert not backups.read(config)["pending_moc"]
    scheduler.tick(config, now=now + 700000)
    assert len(backups.status(config)["jobs"]) == 4


def test_daily_and_debounce_logic(config, configured):
    import datetime as dt

    from fiber_bundle.backup_automation.scheduler import due

    value = backups.read(config)
    noon = dt.datetime.now().astimezone().replace(hour=12, minute=0, second=0).timestamp()
    value.update(dirty_since=noon - 200, last_change=noon - 200, configured_at=noon - 86400)
    value["policy"].update(enabled=True, daily_time="10:00")
    assert due(value, noon) == "daily"
    value["baseline"] = {"at": noon - 60}
    assert due(value, noon) is None
    value["policy"].update(daily_time="", on_change=True, debounce_seconds=300)
    assert due(value, noon) is None
    assert due(value, noon + 101) == "business_change"


def test_heartbeat_and_backup_writes_do_not_trigger(config, configured):
    from fiber_bundle.backup_automation import changes

    with database.connect(config.state_root) as db:
        db.execute(
            "CREATE TABLE synthetic_status (id TEXT PRIMARY KEY, value TEXT, updated_at REAL)"
        )
        db.execute("INSERT INTO synthetic_status VALUES ('1','business',1)")
    first = changes.observe(config)
    with database.connect(config.state_root) as db:
        db.execute("UPDATE synthetic_status SET updated_at=42")
    (config.state_root / "backend.json").write_text('{"heartbeat":42}')
    with backups.edit(config) as value:
        value["error"] = "backup_synthetic"
    assert changes.observe(config)["sha256"] == first["sha256"]
    with database.connect(config.state_root) as db:
        db.execute("UPDATE synthetic_status SET value='changed'")
    assert changes.observe(config)["sha256"] != first["sha256"]


def test_terminal_moc_event_only_and_idempotent(config, configured):
    from fiber_bundle.organization import store as org

    enable(config, after_moc=True)
    data = {"state": "running", "items": [{"state": "done", "changed": 1}]}
    org.put(config, "jobs", "synthetic", "synthetic-vault", data)
    assert not backups.read(config).get("pending_moc")
    data["state"] = "completed"
    org.put(config, "jobs", "synthetic", "synthetic-vault", data)
    org.put(config, "jobs", "synthetic", "synthetic-vault", {**data, "index_state": "ready"})
    assert len(backups.read(config)["pending_moc"]) == 1


def test_failed_automatic_run_does_not_advance_or_loop(config, configured, monkeypatch):
    import time

    from fiber_bundle.backup_automation import scheduler

    enable(config, interval_minutes=1)
    now = time.time() + 120
    scheduler.tick(config, now=now)

    def fail(*args, **kwargs):
        raise InvalidInput("backup_disk_full")

    monkeypatch.setattr(backup_bundle, "create", fail)
    for _ in range(3):
        with backups.edit(config) as value:
            value["jobs"][0]["next_at"] = 0
        runner.execute(config)
    value = backups.read(config)
    assert value["jobs"][0]["state"] == "failed" and value["baseline"] is None
    assert value["dirty_since"] is not None
    scheduler.tick(config, now=now + 3600)
    assert len(backups.status(config)["jobs"]) == 1


def test_retention_keeps_pins_latest_and_buckets(config, configured):
    import time

    from fiber_bundle.backup_automation import retention

    value = backups.status(config)
    backups.save(
        config,
        Save(
            revision=value["revision"],
            policy=Policy(**{**value["policy"], "keep_recent": 1, "keep_days": 0, "keep_weeks": 0}),
        ),
    )
    backups.enqueue(config, "manual")
    runner.execute(config)
    pinned = backups.status(config)["jobs"][0]
    backups.enqueue(config, "interval")
    runner.execute(config)
    automatic = backups.status(config)["jobs"][0]
    backups.enqueue(config, "interval")
    runner.execute(config)
    jobs = backups.status(config)["jobs"]
    assert jobs[0]["state"] == "succeeded"
    assert jobs[1]["id"] == automatic["id"] and jobs[1]["state"] == "deleted"
    assert Path(pinned["destination"]).exists()
    assert not Path(automatic["destination"]).exists()
    # User files outside the owned repository remain untouched.
    foreign = configured / "personal-photo.txt"
    foreign.write_text("keep me")
    retention.clean(config, now=time.time())
    assert foreign.read_text() == "keep me"
    sample = [
        {"id": str(i), "completed": time.time() - i * 86400, "pinned": i == 8} for i in range(10)
    ]
    keep = retention.keep(sample, {"keep_recent": 1, "keep_days": 3, "keep_weeks": 1}, time.time())
    assert {"0", "1", "2", "8"} <= keep


def test_retention_refuses_modified_latest_before_deleting_older(config, configured):
    import time

    from fiber_bundle.backup_automation import retention

    for _ in range(3):
        backups.enqueue(config, "manual")
        runner.execute(config)
    jobs = backups.status(config)["jobs"]
    backups.pin(config, jobs[1]["id"], False)
    backups.pin(config, jobs[2]["id"], False)
    with backups.edit(config) as value:
        value["policy"].update(keep_recent=1, keep_days=0, keep_weeks=0)
    latest = Path(jobs[0]["destination"]) / "state.fbenc"
    latest.write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        retention.clean(config, now=time.time())
    assert Path(jobs[2]["destination"]).exists()


def test_capacity_fails_without_deleting_pins_or_success_baseline(config, configured):
    backups.enqueue(config, "manual")
    runner.execute(config)
    baseline = backups.read(config)["baseline"]
    status = backups.status(config)
    backups.save(
        config,
        Save(revision=status["revision"], policy=Policy(**{**status["policy"], "max_total_mb": 1})),
    )
    root = config.vault_path(dict(config.vaults)["personal"])
    (root / "large.md").write_bytes(b"x" * (1024**2 + 1))
    backups.enqueue(config, "manual")
    runner.execute(config)
    failed = backups.status(config)["jobs"][0]
    assert failed["state"] == "failed" and failed["error"] == "backup_capacity_limit"
    assert not Path(failed["destination"]).exists()
    assert backups.read(config)["baseline"] == baseline
    assert Path(backups.status(config)["jobs"][1]["destination"]).exists()


def test_durable_terminal_job_receipt_recovers_lost_notification(config, configured):
    import time

    from fiber_bundle.backup_automation import events, scheduler
    from fiber_bundle.organization import store as org

    # Simulate an old completed batch before enabling the policy; no historical replay.
    old = {"id": "old", "state": "completed", "items": [{"state": "done", "changed": 1}]}
    org.put(config, "jobs", "old", "v", old)
    enable(config, after_moc=True)
    events.reconcile(config)
    assert not backups.read(config).get("pending_moc")
    # The native transaction was committed but a process died before the notification.
    with org.connection(config, write=True) as db:
        db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?)",
            ("new", "v", json.dumps({**old, "id": "new"}), time.time()),
        )
    scheduler.tick(config, now=time.time() + 31)
    assert backups.status(config)["jobs"][0]["reason"] == "moc_batch"


def test_draft_package_structure_and_moc_human_content_restore(config, configured):
    root = config.vault_path(dict(config.vaults)["personal"])
    files = {
        "00-Inbox/fake-synthetic/manifest.json": b'{"schema_version":"synthetic-reference-draft"}',
        "00-Inbox/fake-synthetic/chapters/01.md": b"# Synthetic chapter\n![[../images/figure.png]]",
        "00-Inbox/fake-synthetic/images/figure.png": b"synthetic-figure",
        "02-Map/Human MOC.md": "# 人工内容\n保留原文\n".encode(),
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    backups.enqueue(config, "manual")
    runner.execute(config)
    job = backups.status(config)["jobs"][0]
    destination = config.project_root / "draft-recovery"
    backup_restore.restore(Path(job["destination"]), destination, KEY)
    restored = load_config(destination / "config.test.json", destination)
    newroot = restored.vault_path(dict(restored.vaults)["personal"])
    for rel, data in files.items():
        assert (root / rel).read_bytes() == data and (newroot / rel).read_bytes() == data
    assert (
        json.loads((restored.state_root / "restore-gate.json").read_text())["state"]
        == "restored_locked"
    )


def test_cleanup_intent_survives_partial_unlink(config, configured, monkeypatch):
    import time

    from fiber_bundle.backup_automation import retention

    for _ in range(3):
        backups.enqueue(config, "manual")
        runner.execute(config)
    jobs = backups.status(config)["jobs"]
    for job in jobs[1:]:
        backups.pin(config, job["id"], False)
    with backups.edit(config) as state:
        state["policy"].update(keep_recent=1, keep_days=0, keep_weeks=0)
    victim = Path(jobs[2]["destination"])
    original = Path.unlink

    def interrupted(path, *args, **kwargs):
        if path == victim / "vaults.fbenc":
            raise OSError("synthetic interruption")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupted)
    with pytest.raises(OSError):
        retention.clean(config, now=time.time())
    assert (
        next(j for j in backups.status(config)["jobs"] if j["id"] == jobs[2]["id"])["state"]
        == "deleting"
    )
    assert Path(jobs[0]["destination"]).exists()
    runner.recover(config)
    assert (
        next(j for j in backups.status(config)["jobs"] if j["id"] == jobs[2]["id"])["state"]
        == "cleanup_interrupted"
    )
    monkeypatch.setattr(Path, "unlink", original)
    retention.clean(config, now=time.time())
    assert not victim.exists() and Path(jobs[0]["destination"]).exists()


def test_backup_assets_are_registered_and_limits_explicit(http, config, monkeypatch, capsys):
    import sys

    from fiber_bundle import backup_cli

    for name in ("backup_automation.js", "backup_automation_api.js", "backup_automation.css"):
        assert http.get("/static/" + name).status_code == 200
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
            "--max-entries",
            "0",
        ],
    )
    assert backup_cli.main() == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "backup_limits_invalid"


def test_home_backup_overview_matches_authenticated_result(config, configured, http):
    assert http.get("/admin/backup-automation/overview").json() == []
    backups.enqueue(config, "manual")
    runner.execute(config)
    data = http.get("/admin/backup-automation/overview").json()
    assert data[0]["state"] == "backup_verified"
    assert "destination" not in data[0]


def test_background_keychain_access_is_scoped_and_failure_does_not_prompt(
    config, configured, monkeypatch
):
    from fiber_bundle.model_configuration import keychain
    from fiber_bundle.model_configuration.errors import ModelError

    seen = []

    def unavailable(account):
        # The explicit context applies to both the backup key and model-master exports.
        seen.append(keychain._interactive.get())
        raise ModelError("model_keychain_unavailable")

    monkeypatch.setattr(keychain, "get", unavailable)
    backups.enqueue(config, "manual")
    runner.execute(config)
    assert seen == [False]
    assert keychain._interactive.get() is True
    job = backups.status(config)["jobs"][0]
    assert job["state"] == "failed" and job["error"] == "model_keychain_unavailable"
    assert backups.read(config)["baseline"] is None


def test_backup_status_remains_readable_and_other_http_waits_are_bounded(config, configured, http):
    import fcntl
    import os
    import time

    fd = os.open(backups.directory(config) / "runner.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with database.locked(config):
            start = time.monotonic()
            assert http.get("/admin/backup-automation").status_code == 200
            result = http.get("/admin/vaults")
            assert result.status_code == 503 and result.json()["error"] == "backup_snapshot_busy"
            assert time.monotonic() - start < 2
        assert http.get("/admin/vaults").status_code == 200
    finally:
        os.close(fd)
