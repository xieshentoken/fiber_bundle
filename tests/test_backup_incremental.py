"""Isolated shared snapshots: originals, historical receipts and real data are never touched."""

import hashlib
import json
from pathlib import Path

import pytest
from test_backup_automation import configured as configured

from fiber_bundle import backup_bundle, backup_restore
from fiber_bundle.backup_incremental import snapshot
from fiber_bundle.config import IntegrityError, load_config

KEY = "a1" * 32


def test_shared_snapshots_skip_unchanged_reads_and_restore_deletes_renames(
    config, store, request_data, tmp_path_factory
):
    store.ingest(**request_data)
    root = config.vault_path(dict(config.vaults)["personal"])
    (root / "keep.md").write_text("unchanged " * 3000)
    (root / "edit.md").write_text("old body")
    (root / "delete.md").write_text("removed body")
    (root / "rename.md").write_text("renamed body")
    (root / "empty.md").write_bytes(b"")
    (root / "draft").mkdir()
    (root / "draft/part.md").write_text("nested Draft-like structure")
    repository = tmp_path_factory.mktemp("encrypted-shared")
    first = repository / "first"
    initial = snapshot.create(config, first, KEY)
    repo, before = snapshot.read(first, KEY)
    assert initial["stats"]["new_files"] > 0
    old_objects = {p.name: p.read_bytes() for p in repo.home.glob("*.fbobj")}
    (root / "edit.md").write_text("updated body")
    (root / "delete.md").unlink()
    (root / "rename.md").rename(root / "renamed.md")
    second = repository / "second"
    result = snapshot.create(config, second, KEY, base=first)
    assert result["stats"]["reused_bytes"] >= (root / "keep.md").stat().st_size
    assert result["stats"]["new_files"] == 1
    assert result["stats"]["new_bytes"] == len(b"updated body")
    assert all((repo.home / name).read_bytes() == content for name, content in old_objects.items())
    snapshot.verify(second, KEY)
    destination = config.project_root / "restored-incremental"
    backup_restore.restore(second, destination, KEY)
    restored = load_config(destination / "config.test.json", destination)
    actual = restored.vault_path(dict(restored.vaults)["personal"])
    assert (actual / "keep.md").read_bytes() == (root / "keep.md").read_bytes()
    assert (actual / "edit.md").read_text() == "updated body"
    assert not (actual / "delete.md").exists()
    assert not (actual / "rename.md").exists()
    assert (actual / "renamed.md").read_text() == "renamed body"
    assert (actual / "empty.md").read_bytes() == b""
    assert (actual / "draft/part.md").read_text() == "nested Draft-like structure"
    assert (
        json.loads((restored.state_root / "restore-gate.json").read_text())["state"]
        == "restored_locked"
    )
    assert "sha256" in before["manifest"]


def test_missing_and_corrupt_shared_objects_fail_authentication(config, store, tmp_path_factory):
    root = config.vault_path(dict(config.vaults)["personal"])
    (root / "note.md").write_text("synthetic private sentinel")
    repository = tmp_path_factory.mktemp("encrypted-shared-invalid")
    bundle = repository / "first"
    snapshot.create(config, bundle, KEY)
    repo, value = snapshot.read(bundle, KEY)
    assert b"synthetic private sentinel" not in (bundle / "snapshot.fbenc").read_bytes()
    with pytest.raises(IntegrityError):
        snapshot.read(bundle, "b2" * 32)
    identity = value["references"][0]
    path = repo.path(identity)
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(IntegrityError, match="changed"):
        snapshot.verify(bundle, KEY, full=False)
    path.write_bytes(original)
    path.unlink()
    with pytest.raises(IntegrityError, match="missing"):
        snapshot.verify(bundle, KEY)
    with pytest.raises(IntegrityError, match="missing"):
        backup_restore.restore(bundle, config.project_root / "missing-object-restore", KEY)


def test_legacy_full_backup_remains_readable(config, store, tmp_path_factory):
    folder = tmp_path_factory.mktemp("legacy-full")
    backup_bundle.create(config, folder / "v1", KEY)
    result = backup_restore.restore(folder / "v1", config.project_root / "legacy-restored", KEY)
    assert result["original_hashes_verified"]


def test_gui_runner_uses_incremental_then_retains_shared_dependencies(config, configured):
    import time

    from fiber_bundle.backup_automation import retention, runner
    from fiber_bundle.backup_automation import store as backups
    from fiber_bundle.backup_automation.policy import Policy, Save

    value = backups.read(config)
    policy = Policy(
        **{
            **value["policy"],
            "keep_recent": 1,
            "keep_days": 0,
            "keep_weeks": 0,
        }
    )
    backups.save(
        config, Save(revision=value["revision"], policy=policy, storage_mode="incremental")
    )
    root = config.vault_path(dict(config.vaults)["personal"])
    (root / "kept.md").write_text("shared content")
    backups.enqueue(config, "interval")
    assert runner.execute(config)
    first = backups.status(config)["jobs"][0]
    assert first["state"] == "succeeded", first
    assert first["backup_format"] == snapshot.SCHEMA
    assert first["verification"] == "full"
    (root / "added.md").write_text("new content")
    backups.enqueue(config, "interval")
    assert runner.execute(config)
    second = backups.status(config)["jobs"][0]
    assert second["state"] == "succeeded", second
    assert second["verification"] == "incremental"
    assert second["stats"]["reused_files"] > 0
    assert not Path(first["destination"]).exists()
    snapshot.verify(Path(second["destination"]), KEY)
    retention.clean(config, now=time.time())
    snapshot.verify(Path(second["destination"]), KEY)


def test_cached_observation_detects_business_changes_and_deletes(config, store):
    from fiber_bundle import auth, database
    from fiber_bundle.backup_automation import changes

    cache = {}
    first = changes.observe(config, cache)
    assert changes.observe(config, cache) == first
    with database.connect(config.state_root) as db:
        auth.create_client(db, "incremental cache test", False)
    second = changes.observe(config, cache)
    assert first["sha256"] != second["sha256"]


def test_unchanged_payload_is_not_reopened_and_identical_files_share_one_object(
    config, store, tmp_path_factory, monkeypatch
):
    import os

    root = config.vault_path(dict(config.vaults)["personal"])
    large = root / "unchanged.bin"
    large.write_bytes(b"unchanged-content" * 65536)
    (root / "same.bin").write_bytes(large.read_bytes())
    repository = tmp_path_factory.mktemp("shared-read-audit")
    first = repository / "first"
    snapshot.create(config, first, KEY)
    repo, value = snapshot.read(first, KEY)
    files = [entry for entry in value["manifest"]["entries"] if entry["kind"] == "file"]
    refs = {(e["section"], e["path"]): r for e, r in zip(files, value["references"], strict=True)}
    vault = "vault:" + next(v["id"] for v in value["manifest"]["vaults"] if v["name"] == "personal")
    assert refs[(vault, "unchanged.bin")] == refs[(vault, "same.bin")]
    opened = []
    original_open = os.open

    def audit(path, *args, **kwargs):
        if Path(path) == large:
            opened.append(str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", audit)
    result = snapshot.create(config, repository / "second", KEY, base=first)
    assert opened == []
    assert result["stats"]["new_files"] == 0
    assert result["stats"]["new_bytes"] == 0
    assert result["stats"]["hashed_bytes"] == 0
    snapshot.verify(repository / "second", KEY)
    assert repo.namespace == result["namespace"]


def test_pinned_snapshots_protect_shared_data_until_last_reference_is_removed(config, configured):
    import time

    from fiber_bundle.backup_automation import retention, runner
    from fiber_bundle.backup_automation import store as backups
    from fiber_bundle.backup_automation.policy import Policy, Save

    state = backups.read(config)
    policy = Policy(
        **{
            **state["policy"],
            "keep_recent": 1,
            "keep_days": 0,
            "keep_weeks": 0,
        }
    )
    backups.save(
        config, Save(revision=state["revision"], policy=policy, storage_mode="incremental")
    )
    root = config.vault_path(dict(config.vaults)["personal"])
    (root / "removed.md").write_text("unique removed content")
    backups.enqueue(config, "manual")
    assert runner.execute(config)
    first = backups.status(config)["jobs"][0]
    repo, value = snapshot.read(Path(first["destination"]), KEY)
    identity = repo.identity(
        hashlib.sha256(b"unique removed content").hexdigest(), len(b"unique removed content")
    )
    (root / "removed.md").unlink()
    backups.enqueue(config, "interval")
    assert runner.execute(config)
    assert repo.path(identity).exists()
    backups.pin(config, first["id"], False)
    retention.clean(config, now=time.time())
    assert not repo.path(identity).exists()
    latest = backups.status(config)["jobs"][0]
    snapshot.verify(Path(latest["destination"]), KEY)
    assert "manifest" in value


def test_full_cli_verification_authenticates_shared_objects(
    config, store, tmp_path_factory, monkeypatch, capsys
):
    import getpass
    import sys

    from fiber_bundle import backup_cli

    root = tmp_path_factory.mktemp("shared-cli")
    snapshot.create(config, root / "snapshot", KEY)
    monkeypatch.setattr(
        sys,
        "argv",
        ["fiber-backup", "verify", "--bundle", str(root / "snapshot"), "--authenticate"],
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(getpass, "getpass", lambda _: KEY)
    assert backup_cli.main() == 0
    value = json.loads(capsys.readouterr().out)
    assert value["authenticated"] and not value["restorable"]
    assert KEY not in json.dumps(value)


def test_recovery_key_rotation_starts_a_new_namespace_and_keeps_old_backups(config, configured):
    from fiber_bundle.backup_automation import runner
    from fiber_bundle.backup_automation import store as backups
    from fiber_bundle.backup_automation.policy import Policy, Save

    state = backups.read(config)
    policy = Policy(**{**state["policy"]})
    backups.save(
        config, Save(revision=state["revision"], policy=policy, storage_mode="incremental")
    )
    backups.enqueue(config, "manual")
    assert runner.execute(config)
    old = backups.status(config)["jobs"][0]
    state = backups.read(config)
    other = "b2" * 32
    backups.save(
        config, Save(revision=state["revision"], policy=policy, recovery_key=other, key_saved=True)
    )
    backups.enqueue(config, "manual")
    assert runner.execute(config)
    latest = backups.status(config)["jobs"][0]
    assert latest["state"] == "succeeded", latest
    assert old["namespace"] != latest["namespace"]
    snapshot.verify(Path(old["destination"]), KEY)
    snapshot.verify(Path(latest["destination"]), other)
    with pytest.raises(IntegrityError):
        snapshot.verify(Path(old["destination"]), other)


def test_source_changed_during_incremental_capture_never_publishes_completion(
    config, store, tmp_path_factory, monkeypatch
):
    from fiber_bundle.backup_incremental import objects

    root = config.vault_path(dict(config.vaults)["personal"])
    note = root / "note.md"
    note.write_text("original")
    repository = tmp_path_factory.mktemp("shared-source-change")
    first = repository / "first"
    snapshot.create(config, first, KEY)
    note.write_text("updated")
    original_save = objects.save

    def changed(repo, source, entry, prior):
        value = original_save(repo, source, entry, prior)
        if source == note:
            note.write_text("external modification")
        return value

    monkeypatch.setattr(objects, "save", changed)
    with pytest.raises(IntegrityError, match="source_changed"):
        snapshot.create(config, repository / "incomplete", KEY, base=first)
    assert not (repository / "incomplete/bundle.json").exists()
    snapshot.verify(first, KEY)


def test_cleanup_cannot_race_an_active_writer(config, configured):
    import fcntl
    import os
    import time

    from fiber_bundle.backup_automation import retention
    from fiber_bundle.backup_automation import store as backups
    from fiber_bundle.config import InvalidInput

    lock = os.open(backups.directory(config) / "runner.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(InvalidInput, match="backup_job_busy"):
            retention.clean(config, now=time.time())
    finally:
        os.close(lock)


def test_storage_settings_are_separate_from_the_legacy_trigger_policy(config, configured, http):
    from fiber_bundle.backup_automation import store as backups

    status = backups.status(config)
    body = {
        "revision": status["revision"],
        "policy": status["policy"],
        "storage_mode": "incremental",
        "full_verify_days": 14,
    }
    response = http.put("/admin/backup-automation", json=body)
    assert response.status_code == 200
    value = response.json()
    assert value["storage_mode"] == "incremental"
    assert value["full_verify_days"] == 14
    assert "incremental" not in value["policy"]
    assert "full_verify_days" not in value["policy"]
    assert value["jobs"] == []
    bad = {**body, "revision": value["revision"], "full_verify_days": 0}
    assert http.put("/admin/backup-automation", json=bad).status_code == 422
    assert backups.status(config)["full_verify_days"] == 14
