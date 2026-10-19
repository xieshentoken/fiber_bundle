"""Progress and online reservations use isolated repositories and subprocesses only."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from test_backup_automation import configured as configured

from fiber_bundle import backup_bundle, backup_preflight
from fiber_bundle.backup_automation import online, runner, verification
from fiber_bundle.backup_automation import store as backups
from fiber_bundle.backup_incremental import snapshot
from fiber_bundle.backup_inventory import Inventory, Limits
from fiber_bundle.config import IntegrityError, InvalidInput

KEY = "a1" * 32


def test_capacity_rejects_before_any_source_body_open(config, store, monkeypatch):
    source = config.vault_path(dict(config.vaults)["personal"]) / "oversize.txt"
    source.write_bytes(b"x" * 2000)
    original = os.open
    opened = []

    def spy(path, *args, **kwargs):
        if Path(path) == source:
            opened.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    with pytest.raises(InvalidInput, match="backup_total_limit"):
        backup_preflight.capacity(config, limits=Limits(total_bytes=100))
    assert not opened


def test_capacity_failure_does_not_read_key_or_create_repository(config, configured, monkeypatch):
    from fiber_bundle.backup_automation.policy import Policy, Save
    from fiber_bundle.model_configuration import keychain

    state = backups.read(config)
    backups.save(
        config,
        Save(
            revision=state["revision"], policy=Policy(**{**state["policy"], "max_snapshot_mb": 1})
        ),
    )
    (config.vault_path(dict(config.vaults)["personal"]) / "large.txt").write_bytes(
        b"x" * 2 * 1024**2
    )
    monkeypatch.setattr(keychain, "get", lambda *_: pytest.fail("Key must not be read"))
    backups.enqueue(config, "manual")
    runner.execute(config)
    job = backups.status(config)["jobs"][0]
    assert job["state"] == "failed" and job["error"] == "backup_total_limit"
    assert job["progress"]["phase"] == "capacity"
    assert "destination" not in job
    assert list(configured.iterdir()) == []


def test_raw_backup_descriptors_cannot_release_another_process_reservation(config, store):
    path = config.state_root / "message-channels.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE synthetic_lock_check (id INTEGER)")
        db.execute("PRAGMA user_version=3")
    command = [
        sys.executable,
        "-c",
        (
            "import sqlite3,sys;d=sqlite3.connect(sys.argv[1],timeout=0);"
            'd.execute("INSERT INTO synthetic_lock_check VALUES (1)");d.commit()'
        ),
        str(path),
    ]
    with online.lease(config, Limits()):
        # Closing any raw descriptor in the lock-owning process releases POSIX
        # locks. The backup reader now has a separate lock-owning process.
        path.read_bytes()
        attempt = subprocess.run(command, capture_output=True)
        assert attempt.returncode != 0
        assert b"locked" in attempt.stderr
    assert subprocess.run(command, capture_output=True).returncode == 0
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM synthetic_lock_check").fetchone()[0] == 1


def test_metadata_churn_rechecks_body_and_keeps_real_change_protection(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    source = root / "sample.txt"
    source.write_text("original")
    inventory = Inventory(Limits())
    inventory.tree(root, "state", skip=frozenset({"transient-journal"}))
    source.chmod(0o600)  # Match initial permissions first; changing permissions remains rejected.
    # Capture after the intentional permission change.
    inventory = Inventory(Limits())
    inventory.tree(root, "state", skip=frozenset({"transient-journal"}))
    source.chmod(0o600)
    (root / "transient-journal").write_text("temporary coordination")
    inventory.unchanged()
    source.write_text("modified")
    with pytest.raises(IntegrityError, match="backup_source_changed") as error:
        inventory.unchanged()
    assert error.value.details == {"section": "state", "path": "sample.txt"}


def test_included_directory_membership_change_is_not_ignored(tmp_path):
    (tmp_path / "a.txt").write_text("one")
    inventory = Inventory(Limits())
    inventory.tree(tmp_path, "state")
    (tmp_path / "new.txt").write_text("two")
    with pytest.raises(IntegrityError, match="backup_source_changed"):
        inventory.unchanged()


@pytest.mark.parametrize("incremental", [False, True])
def test_stages_have_independent_counts_and_complete_authentication(
    config, store, tmp_path_factory, incremental
):
    output = tmp_path_factory.mktemp("backup-stages") / "snapshot"
    events = []
    create = snapshot.create if incremental else backup_bundle.create
    result = create(config, output, KEY, online=True, progress=events.append)
    verification.verify(output, KEY, progress=events.append)
    phases = [e["phase"] for e in events]
    assert phases.index("scanning") < phases.index("encrypting") < phases.index("verifying")
    for phase in ["scanning", "encrypting", "verifying"]:
        last = next(e for e in reversed(events) if e["phase"] == phase)
        assert last["complete"] and last["files_done"] > 0
    assert (output / "bundle.json").exists()
    assert result["state"] == "complete"


def test_progress_does_not_rewrite_policy_or_observation_ledger(config, configured):
    from fiber_bundle.backup_automation.progress import Reporter

    job = backups.enqueue(config, "manual")
    ledger = backups.directory(config) / "state.json"
    before = ledger.read_bytes()
    report = Reporter(config, job["id"], job["attempts"])
    report({"phase": "scanning", "files_done": 10, "bytes_done": 100})
    report({"phase": "scanning", "files_done": 20, "bytes_done": 200, "complete": True})
    assert ledger.read_bytes() == before
    value = backups.status(config)["jobs"][0]
    assert value["progress"]["files_done"] == 20
    assert "observation" not in value and "secret_account" not in value


@pytest.mark.parametrize("incremental", [False, True])
def test_transport_writes_resume_while_encrypting_a_fixed_database_copy(
    config, store, tmp_path_factory, incremental
):
    path = config.state_root / "message-channels.sqlite3"
    from fiber_bundle.channels import store as channels

    with channels.opened(config) as db:
        db.execute("CREATE TABLE transport_probe (id INTEGER)")
    observed = []

    def progress(value):
        if value["phase"] == "encrypting" and not observed:
            command = [
                sys.executable,
                "-c",
                (
                    "import sqlite3,sys;d=sqlite3.connect(sys.argv[1],timeout=0);"
                    'd.execute("INSERT INTO transport_probe VALUES (1)");d.commit()'
                ),
                str(path),
            ]
            result = subprocess.run(command, capture_output=True)
            assert result.returncode == 0, result.stderr
            observed.append(True)

    out = tmp_path_factory.mktemp("frozen-db-copy") / "snapshot"
    (snapshot.create if incremental else backup_bundle.create)(
        config, out, KEY, online=True, progress=progress
    )
    verification.verify(out, KEY)
    from fiber_bundle import backup_restore

    backup_restore.restore(out, config.project_root / "restored-transport-probe", KEY)
    with sqlite3.connect(
        config.project_root
        / "restored-transport-probe"
        / config.state_relative
        / "message-channels.sqlite3"
    ) as db:
        assert db.execute("SELECT count(*) FROM transport_probe").fetchone()[0] == 0
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM transport_probe").fetchone()[0] == 1
    assert observed
    assert not list(backups.directory(config).glob("database-snapshot-*"))


def test_database_copies_are_private_independent_and_removed(config, store):
    import stat

    captured = []
    with online.snapshots(config, Limits()) as copies:
        assert copies
        for name, path in copies.items():
            assert not path.is_relative_to(config.project_root)
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert path.stat().st_nlink == 1
            assert (path.stat().st_dev, path.stat().st_ino) != (
                (config.state_root / name).stat().st_dev,
                (config.state_root / name).stat().st_ino,
            )
            captured.append(path)
    assert all(not path.exists() for path in captured)
