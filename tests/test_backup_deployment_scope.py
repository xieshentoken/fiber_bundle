"""Legacy deployed layouts round trip without silently dropping evidence or activating it."""

import fcntl
import os

import pytest

from fiber_bundle import backup_bundle, backup_preflight, backup_requests
from fiber_bundle.config import IntegrityError, InvalidInput, load_config

PASSWORD = "synthetic-backup-password-only"
ARCHIVES = (
    "deployment-backups",
    "m4-acceptance",
    "m5-acceptance",
    "m6-acceptance",
    "m8-acceptance",
    "m8-runtime",
)


def deployment_files(config):
    values = {
        "deployed-ui.json": b'{"pid":123,"deployment":"synthetic-old-host"}',
        ".DS_Store": b"synthetic system metadata",
        **{name + "/snapshot/metadata.sqlite3": b"opaque historical bytes" for name in ARCHIVES},
    }
    for name, data in values.items():
        path = config.state_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return values


def test_deployment_scope_preserved_in_encrypted_restore_without_start(config, store):
    files = deployment_files(config)
    preflight = backup_preflight.inspect(config)
    scope = {entry["path"] for entry in preflight["entries"] if entry["section"] == "state"}
    assert files.keys() <= scope
    intent = backup_requests.prepare(config, "create")
    result = backup_requests.execute(config, intent["id"], PASSWORD)
    assert result["state"] == "complete"
    restore = backup_requests.prepare(config, "restore", intent["id"])
    result = backup_requests.execute(config, restore["id"], PASSWORD)
    assert result["state"] == "restored_locked" and result["automatic_start"] is False
    target = load_config(
        config.project_root / "restores" / restore["id"] / "config.test.json",
        config.project_root / "restores" / restore["id"],
    )
    assert all((target.state_root / name).read_bytes() == data for name, data in files.items())
    assert all((config.state_root / name).read_bytes() == data for name, data in files.items())


def test_prepare_and_execute_refuse_unknown_scope_without_intent(config, store, http):
    (config.state_root / "unclassified-business-data").mkdir()
    with pytest.raises(InvalidInput, match="backup_unclassified_state"):
        backup_requests.prepare(config, "create")
    assert not (config.project_root / ".backup-requests").exists()
    response = http.post("/admin/backups", json={"action": "create"})
    assert response.status_code == 422
    assert response.json() == {"error": "backup_unclassified_state"}
    with pytest.raises(InvalidInput, match="backup_unclassified_state"):
        backup_bundle.create(config, config.project_root / "bundle", PASSWORD)


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "fifo"])
def test_legacy_archives_do_not_relax_file_safety(config, store, tmp_path, unsafe):
    root = config.state_root / "deployment-backups"
    root.mkdir()
    original = tmp_path / "private-sentinel"
    original.write_bytes(b"untouched")
    target = root / "unsafe"
    if unsafe == "symlink":
        target.symlink_to(original)
    elif unsafe == "hardlink":
        os.link(original, target)
    else:
        os.mkfifo(target)
    with pytest.raises(InvalidInput):
        backup_preflight.inspect(config)
    assert original.read_bytes() == b"untouched"


def test_prepare_is_lightweight_but_execute_refuses_live_archive_owner(config, store):
    root = config.state_root / "m8-runtime"
    root.mkdir()
    lock = root / "worker.lock"
    lock.touch()
    with lock.open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        intent = backup_requests.prepare(config, "create")
        with pytest.raises(InvalidInput, match="backup_runtime_busy"):
            backup_requests.execute(config, intent["id"], PASSWORD)
    assert not (config.project_root / "backups" / intent["id"]).exists()


def test_scope_changed_after_preparation_is_refused(config, store):
    deployment_files(config)
    intent = backup_requests.prepare(config, "create")
    (config.state_root / "new-business-state").mkdir()
    with pytest.raises(InvalidInput, match="backup_unclassified_state"):
        backup_requests.execute(config, intent["id"], PASSWORD)


def test_legacy_archive_change_during_offline_scan_is_refused(config, store, monkeypatch):
    deployment_files(config)
    original = backup_preflight.Inventory.unchanged

    def mutate(inventory):
        (config.state_root / "m6-acceptance/snapshot/metadata.sqlite3").write_bytes(b"changed")
        original(inventory)

    monkeypatch.setattr(backup_preflight.Inventory, "unchanged", mutate)
    with pytest.raises(IntegrityError, match="backup_source_changed"):
        backup_preflight.inspect(config)
