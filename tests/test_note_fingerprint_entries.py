"""Native batching preserves the prior fingerprint bytes and protected scope boundary."""

import hashlib
import json
import subprocess
from unittest.mock import patch

import pytest
from test_cloud_directory_status import native as native

from fiber_bundle import cloud_files, note_fingerprint_entries, vault_scope
from fiber_bundle.config import IntegrityError


def configure(monkeypatch, native):
    helper = native / "helper"
    monkeypatch.setenv("FIBER_CLOUD_HELPER", str(helper))
    monkeypatch.setenv("FIBER_CLOUD_HELPER_SHA256", hashlib.sha256(helper.read_bytes()).hexdigest())


def original(root, scope):
    import stat

    return [
        [path.relative_to(root).as_posix(), *note_fingerprint_entries.signature(info)]
        for path, info in vault_scope.entries(
            root,
            scope,
            hidden=True,
            batch=True,
            file_filter=lambda p: (
                p.name.lower().endswith(".md") or p.name in note_fingerprint_entries.MARKERS
            ),
        )
        if not stat.S_ISDIR(info.st_mode)
    ]


def test_native_inputs_equal_original_with_unicode_order_and_denials(native, tmp_path, monkeypatch):
    configure(monkeypatch, native)
    root = tmp_path / "Mobile Documents/vault"
    root.mkdir(parents=True)
    for name in ["a/inside.md", "a.md", "b/液冷.MD", "café.md", "cafe\u0301.md", "hidden.txt"]:
        path = root / name
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            path.write_text("# synthetic\n")
    (root / ".fiber-analysis.json").write_text("{}")
    (root / ".obsidian").mkdir()
    (root / ".obsidian/conflict.icloud").mkdir()
    protected = root / "01-Projects/protected-project"
    protected.mkdir(parents=True)
    (protected / "not-downloaded.icloud").mkdir()
    (root / "linked.md").symlink_to(root / "a.md")
    scope = vault_scope.Scope(exclude=("01-Projects/protected-project",))
    expected = original(root, scope)
    with patch.object(cloud_files.subprocess, "run", wraps=subprocess.run) as run:
        actual = list(note_fingerprint_entries.entries(root, scope))
    assert actual == expected
    assert [row[0] for row in actual if row[0].startswith("a")][:2] == ["a/inside.md", "a.md"]
    assert run.call_count == 1
    assert not any("protected-project" in row[0] or ".obsidian" in row[0] for row in actual)
    assert list(note_fingerprint_entries.entries(root, scope)) == actual  # Independent pass.


def test_native_subset_matches_original_and_detects_new_or_changed_file(
    native, tmp_path, monkeypatch
):
    configure(monkeypatch, native)
    root = tmp_path / "Mobile Documents/vault"
    (root / "chosen").mkdir(parents=True)
    (root / "excluded.icloud").mkdir()
    path = root / "chosen/current.md"
    path.write_text("# one\n")
    scope = vault_scope.Scope(include=("chosen",))
    before = list(note_fingerprint_entries.entries(root, scope))
    assert before == original(root, scope)
    path.write_text("# changed\n")
    assert list(note_fingerprint_entries.entries(root, scope)) != before
    (root / "chosen/new.md").write_text("# new\n")
    assert len(list(note_fingerprint_entries.entries(root, scope))) == 2
    path.unlink()
    assert len(list(note_fingerprint_entries.entries(root, scope))) == 1
    (root / "chosen/pending.icloud").mkdir()
    with pytest.raises(IntegrityError, match="cloud_item_pending_stale_or_conflicted"):
        list(note_fingerprint_entries.entries(root, scope))


@pytest.mark.parametrize("reason", ["stale", "conflicts", "metadata_unavailable"])
def test_unavailable_native_inventory_never_yields_partial_evidence(tmp_path, monkeypatch, reason):
    root = tmp_path / "Mobile Documents/vault"
    root.mkdir(parents=True)
    report = {
        "schema": "cloud-note-inventory/v1",
        "status": {"schema": "cloud-status/v1", "available": False, reason: 1},
        "root": [],
        "files": [["private.md", 1, 2, 3, 4, 5, 6, 7]],
    }
    monkeypatch.setattr(cloud_files, "_helper", lambda: root / "helper")
    monkeypatch.setattr(
        cloud_files.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(report).encode()),
    )
    with pytest.raises(IntegrityError):
        list(note_fingerprint_entries.entries(root, vault_scope.Scope()))


def test_changed_native_observation_is_rejected_before_yield(tmp_path, monkeypatch):
    root = tmp_path / "Mobile Documents/vault"
    root.mkdir(parents=True)
    path = root / "current.md"
    path.write_text("# before\n")
    observed = ["current.md", *note_fingerprint_entries.signature(path.lstat())]
    report = {"root": note_fingerprint_entries.signature(root.lstat()), "files": [observed]}
    path.write_text("# changed\n")
    monkeypatch.setattr(cloud_files, "scoped_note_inventory", lambda *a: report)
    with pytest.raises(IntegrityError):
        list(note_fingerprint_entries.entries(root, vault_scope.Scope()))
