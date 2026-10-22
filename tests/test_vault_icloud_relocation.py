"""Opt-in iCloud relocation with real scoped helper and real filesystem identities."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_vault_relocation import finish
from test_vault_relocation import relocation as relocation

from fiber_bundle import cloud_files, database, moc_store, vault_scope, vaults
from fiber_bundle.config import IntegrityError
from fiber_bundle.vault_relocation import execution, files, icloud, plan
from fiber_bundle.vault_relocation.errors import RelocationError


@pytest.fixture
def cloud_location(config, relocation, tmp_path, monkeypatch):
    helper = os.environ.get("FIBER_TEST_CLOUD_HELPER")
    if not helper:
        pytest.skip("real macOS scoped cloud-status helper required")
    monkeypatch.setenv("FIBER_CLOUD_HELPER", helper)
    monkeypatch.setenv(
        "FIBER_CLOUD_HELPER_SHA256", hashlib.sha256(Path(helper).read_bytes()).hexdigest()
    )
    source, _, vid, client, mid = relocation
    moved = source.with_name("L2")
    source.rename(moved)
    with database.connect(config.state_root) as db:
        db.execute("UPDATE vaults SET absolute_root=? WHERE id=?", (str(moved), vid))
    container = tmp_path / "Mobile Documents/iCloud~md~obsidian/Documents"
    container.mkdir(parents=True)
    monkeypatch.setattr(icloud, "OBSIDIAN", container)
    return moved, container / "L2_vault", vid, client, mid


def test_opt_in_icloud_keeps_file_note_vault_and_manual_moc_identities(config, cloud_location):
    source, target, vid, client, mid = cloud_location
    before = files.inventory(source)
    manual = moc_store.load(config, vid, mid)
    packet = plan.preview(config, vid, str(target), icloud=True)
    execution.start(config, packet["id"], packet["plan_sha256"])
    result = finish(config, packet)
    assert result["state"] == "complete" and not source.exists()
    assert files.inventory(target, icloud=True, excluded=("01-Projects/GF_supply_chain",)) == before
    assert moc_store.load(config, vid, mid) == manual
    with database.connect(config.state_root) as db:
        row = db.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone()
        assert row["absolute_root"] == str(target)
        assert json.loads(row["scope_json"])["exclude"] == ["01-Projects/GF_supply_chain"]
        assert vaults.checked_vault(config, row) == target
        assert vault_scope.for_vault(target, row).exclude == ("01-Projects/GF_supply_chain",)


@pytest.mark.parametrize(
    "phase", ["quiescing", "checkpointing", "moving", "switching", "verifying"]
)
@pytest.mark.parametrize("action", ["resume", "restore"])
def test_cloud_interruption_can_resume_or_restore_without_new_ingestion(
    config, cloud_location, phase, action
):
    source, target, vid, _, _ = cloud_location
    packet = plan.preview(config, vid, str(target), icloud=True)
    execution.start(config, packet["id"], packet["plan_sha256"])
    for _ in range(8):
        if execution.view(config, packet["id"])["state"] == phase:
            break
        execution.step(config, packet["id"])
    execution.startup(config)
    preview = execution.recovery_preview(config, packet["id"])
    assert preview["recovery"][action], preview
    execution.recover(config, packet["id"], action, preview["recovery"]["sha256"])
    finish(config, packet, "complete" if action == "resume" else "restored")
    assert (target if action == "resume" else source).is_dir()
    with database.connect(config.state_root) as db:
        row = db.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone()
        assert row["absolute_root"] == str(target if action == "resume" else source)
        if action == "restore":
            assert json.loads(row["scope_json"])["exclude"] == []


def test_cloud_mode_is_explicit_and_container_is_restricted(config, cloud_location):
    source, target, vid, _, _ = cloud_location
    with pytest.raises(RelocationError):
        plan.preview(config, vid, str(target))
    other = target.parent.parent / "other-container"
    other.mkdir()
    with pytest.raises(RelocationError, match="icloud_container_required"):
        plan.preview(config, vid, str(other / "new"), icloud=True)
    assert source.is_dir() and not target.exists()


def test_protected_entries_stop_before_their_metadata_is_read(config, cloud_location, monkeypatch):
    source, target, vid, _, _ = cloud_location
    protected = source / "01-Projects/GF_supply_chain"
    protected.mkdir(parents=True)
    original = Path.lstat

    def stat(path, *args, **kwargs):
        assert path != protected and protected not in path.parents, "protected metadata accessed"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", stat)
    with pytest.raises(RelocationError, match="protected_entries_present"):
        plan.preview(config, vid, str(target), icloud=True)
    assert not target.exists()


def test_real_helper_excludes_before_reading_denied_symlink_metadata(cloud_location, tmp_path):
    helper = os.environ["FIBER_CLOUD_HELPER"]
    root = cloud_location[1].parent / "helper-synthetic"
    root.mkdir()
    (root / "allowed.md").write_text("synthetic")
    (root / "01-Projects").mkdir()
    (root / "01-Projects/GF_supply_chain").symlink_to(tmp_path / "missing-private")
    result = json.loads(
        subprocess.check_output(
            [helper, "--tree-excluding", str(root), json.dumps(["01-Projects/GF_supply_chain"])]
        )
    )
    assert result["available"] is False and result["excluded_present"] == 1
    assert result["errors"] == 0 and result["entries"] == 3


def test_cloud_tree_has_separate_bounded_timeout_and_keeps_conflict_checks(
    cloud_location, monkeypatch
):
    from types import SimpleNamespace

    target = cloud_location[1].parent
    observed = []

    def run(arguments, **options):
        observed.append(options["timeout"])
        return SimpleNamespace(stdout=b'{"schema":"cloud-status/v1","available":true}')

    monkeypatch.setattr(cloud_files.subprocess, "run", run)
    cloud_files.require_available(target)
    cloud_files.require_available(target, tree=True)
    assert observed == [20, 180]

    def expired(arguments, **options):
        raise subprocess.TimeoutExpired(arguments, options["timeout"])

    monkeypatch.setattr(cloud_files.subprocess, "run", expired)
    with pytest.raises(IntegrityError, match="^cloud_status_check_timeout$"):
        cloud_files.require_available(target, tree=True)

    monkeypatch.setattr(
        cloud_files.subprocess,
        "run",
        lambda *args, **options: SimpleNamespace(
            stdout=b'{"schema":"cloud-status/v1","available":false,"conflicts":1}'
        ),
    )
    with pytest.raises(IntegrityError, match="cloud_item_pending_stale_or_conflicted"):
        cloud_files.require_available(target, tree=True)


def test_active_save_policy_follows_carried_protection_without_rewriting_settings(
    config, cloud_location
):
    from fiber_bundle import vault_save_policy as policy
    from fiber_bundle.vault_save_contract import SaveSettings

    _, target, vid, _, _ = cloud_location
    settings = SaveSettings(layout_mode="obsidian_folders")
    with database.connect(config.state_root) as db:
        packet = policy.preview(config, db, vid, settings, 0, enable_publication=True)
        policy.save(config, db, vid, packet["preview_id"], 0)
        assert policy.get(config, db, vid)["publication_ready"]
        saved = tuple(
            db.execute("SELECT * FROM vault_save_policies WHERE vault_id=?", (vid,)).fetchone()
        )
    packet = plan.preview(config, vid, str(target), icloud=True)
    execution.start(config, packet["id"], packet["plan_sha256"])
    finish(config, packet)
    with database.connect(config.state_root) as db:
        assert policy.get(config, db, vid)["publication_ready"]
        assert saved == tuple(
            db.execute("SELECT * FROM vault_save_policies WHERE vault_id=?", (vid,)).fetchone()
        )
        # Newly saved rules must work too, without relying on the old basename.
        packet = policy.preview(config, db, vid, settings, 1, enable_publication=True)
        policy.save(config, db, vid, packet["preview_id"], 1)
        assert policy.get(config, db, vid)["publication_ready"]
        # Losing the carried protection still revokes the complete binding.
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            (json.dumps({"include": [""], "exclude": []}), vid),
        )
        assert not policy.get(config, db, vid)["binding_current"]


def test_batched_scope_preserves_fingerprint_and_never_probes_denied_children(
    config, cloud_location, monkeypatch
):
    from fiber_bundle import note_search_cache

    source, target, vid, _, _ = cloud_location
    packet = plan.preview(config, vid, str(target), icloud=True)
    execution.start(config, packet["id"], packet["plan_sha256"])
    finish(config, packet)
    with database.connect(config.state_root) as db:
        vault = db.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone()
        actual = note_search_cache.fingerprint(config, db, vault)
        original_entries = vault_scope.entries

        def unbatched(*args, **kwargs):
            kwargs.update(batch=False, file_filter=None)
            return original_entries(*args, **kwargs)

        with monkeypatch.context() as context:
            context.setattr(vault_scope, "entries", unbatched)
            assert note_search_cache.fingerprint(config, db, vault) == actual
        protected = target / "01-Projects/GF_supply_chain"
        protected.mkdir(parents=True)
        (protected / "private.md").write_text("not authorized")
        available = cloud_files.require_available_many
        sent = []

        def batch(parent, names):
            assert "GF_supply_chain" not in names
            sent.extend(str(parent / name) for name in names)
            return available(parent, names)

        original_stat = Path.lstat

        def guarded(path, *args, **kwargs):
            assert path != protected and protected not in path.parents
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(cloud_files, "require_available_many", batch)
        monkeypatch.setattr(Path, "lstat", guarded)
        # Denied additions never invalidate or enter the authorized fingerprint.
        note_search_cache.fingerprint(config, db, vault)
        assert not any("GF_supply_chain" in path for path in sent)


def test_real_batch_helper_checks_selected_items_without_scanning_other_children(
    cloud_location, tmp_path
):
    root = cloud_location[1].parent
    (root / "safe.md").write_text("local synthetic note")
    (root / "other").symlink_to(tmp_path / "not-present")
    cloud_files.require_available_many(root, ("safe.md",))
    with pytest.raises(IntegrityError, match="cloud_symlink_not_allowed"):
        cloud_files.require_available_many(root, ("other",))
    with pytest.raises(Exception, match="cloud_path_invalid"):
        cloud_files.require_available_many(root, ("../escape",))
