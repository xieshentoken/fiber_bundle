"""Full-file metadata work has its own finite, fresh, scope-safe budget."""

import hashlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from test_cloud_directory_status import native as native

from fiber_bundle import cloud_files, vault_scope
from fiber_bundle.config import IntegrityError


def files(directory, count):
    directory.mkdir(parents=True, exist_ok=True)
    for number in range(count):
        (directory / f"attachment-{number:05d}.bin").write_bytes(b"")


def test_metadata_budget_is_complete_at_20000_and_old_default_stays_bounded(tmp_path):
    root = tmp_path / "vault"
    files(root, 20000)
    scope = vault_scope.Scope()
    with pytest.raises(IntegrityError, match="vault index entry limit exceeded"):
        list(vault_scope.entries(root, scope))
    observed = list(vault_scope.metadata_entries(root, scope))
    assert len(observed) == 20000
    assert {path.name for path, _ in observed} == {
        f"attachment-{number:05d}.bin" for number in range(20000)
    }
    (root / "one-too-many.bin").write_bytes(b"")
    with pytest.raises(IntegrityError, match="^layout_metadata_inventory_limit$"):
        list(vault_scope.metadata_entries(root, scope))


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS metadata helper")
def test_native_status_is_checked_in_bounded_batches_and_every_call_is_fresh(
    tmp_path, monkeypatch, native
):
    helper = native / "helper"
    monkeypatch.setenv("FIBER_CLOUD_HELPER", str(helper))
    monkeypatch.setenv("FIBER_CLOUD_HELPER_SHA256", hashlib.sha256(helper.read_bytes()).hexdigest())
    root = tmp_path / "Mobile Documents/vault"
    files(root, 600)
    with patch.object(cloud_files.subprocess, "run", wraps=subprocess.run) as calls:
        observed = list(vault_scope.metadata_entries(root, vault_scope.Scope()))
    batches = [call for call in calls.call_args_list if "--items" in call.args[0]]
    assert len(observed) == 600 and len(batches) == 3
    assert all("--file" not in call.args[0] for call in calls.call_args_list)
    changed = root / "attachment-00000.bin"
    changed.write_bytes(b"new contents after previous enumeration")
    with patch.object(cloud_files.subprocess, "run", wraps=subprocess.run) as calls:
        current = dict(vault_scope.metadata_entries(root, vault_scope.Scope()))
    assert current[changed].st_size == changed.stat().st_size
    assert len([call for call in calls.call_args_list if "--items" in call.args[0]]) == 3


def test_metadata_scope_denies_protected_hidden_and_symlink_children_before_inspection(
    tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    protected = root / "01-Projects/protected_project"
    protected.mkdir(parents=True)
    (protected / "secret.icloud").write_bytes(b"excluded")
    hidden = root / ".hidden"
    hidden.mkdir()
    (hidden / "secret.icloud").write_bytes(b"excluded")
    (root / "visible.md").write_text("# Visible\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.icloud").write_bytes(b"excluded")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    original_stat = Path.lstat

    def guarded_stat(path, *args, **kwargs):
        assert path != protected and protected not in path.parents
        assert path != hidden and hidden not in path.parents
        assert path != outside and outside not in path.parents
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", guarded_stat)
    with patch.object(
        cloud_files, "require_available_many", wraps=cloud_files.require_available_many
    ) as checks:
        observed = list(
            vault_scope.metadata_entries(
                root, vault_scope.Scope(exclude=("01-Projects/protected_project",))
            )
        )
    assert {path.relative_to(root).as_posix() for path, _ in observed} == {
        "01-Projects",
        "visible.md",
    }
    assert all(
        not {"protected_project", ".hidden", "linked"}.intersection(call.args[1])
        for call in checks.call_args_list
    )


def test_metadata_cloud_placeholder_and_depth_limits_still_fail_closed(tmp_path):
    root = tmp_path / "pending"
    root.mkdir()
    (root / "visible.md").write_text("# Visible\n")
    (root / "unavailable.icloud").write_bytes(b"pending")
    with pytest.raises(IntegrityError, match="cloud_item_not_downloaded"):
        list(vault_scope.metadata_entries(root, vault_scope.Scope()))
    deep = tmp_path / "deep"
    cursor = deep
    for _ in range(34):
        cursor = cursor / "child"
    cursor.mkdir(parents=True)
    with pytest.raises(IntegrityError, match="vault index depth limit exceeded"):
        list(vault_scope.metadata_entries(deep, vault_scope.Scope()))
