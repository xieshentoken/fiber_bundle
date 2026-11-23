"""Directory names carry no implicit permissions; configured denials still apply."""

import json
from pathlib import Path

import pytest

from fiber_bundle import moc_query_members, moc_write_scope, vault_scope
from fiber_bundle.config import InvalidInput
from fiber_bundle.vault_sync import store


@pytest.mark.parametrize("name", ["L2", "ExampleVault", "ordinary"])
def test_scope_observes_former_special_directory(tmp_path, name):
    root = tmp_path / name
    note = root / "01-Projects/protected_project/visible.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Visible\n")
    scope = vault_scope.effective(root, {"include": [""], "exclude": []})
    relative = note.relative_to(root).as_posix()
    assert not scope.exclude
    assert note in {p for p, _ in vault_scope.entries(root, scope)}
    assert store.accepted(scope, relative)
    assert moc_query_members.readable(scope, relative)
    assert moc_write_scope._readable(scope, relative)
    assert vault_scope.effective(note.parent, {"include": [""], "exclude": []}).allows(note.name)


@pytest.mark.parametrize("excluded", ["01-Projects/protected_project", "private"])
def test_explicit_denial_is_applied_before_child_metadata(tmp_path, monkeypatch, excluded):
    denied = tmp_path / excluded
    denied.mkdir(parents=True)
    note = denied / "secret.md"
    note.write_text("excluded")
    scope = vault_scope.effective(tmp_path, {"include": [""], "exclude": [excluded]})
    original = Path.lstat

    def guarded(path, *args, **kwargs):
        assert path != denied and denied not in path.parents
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", guarded)
    assert denied not in {p for p, _ in vault_scope.entries(tmp_path, scope)}
    relative = note.relative_to(tmp_path).as_posix()
    assert not store.accepted(scope, relative)
    assert not moc_query_members.readable(scope, relative)
    assert not moc_write_scope._readable(scope, relative)


def test_inherited_policy_still_rejects_denied_root_before_metadata(tmp_path, monkeypatch):
    denied = tmp_path / "private"
    denied.mkdir()
    (tmp_path / vault_scope.POLICY).write_text(
        json.dumps({"schema_version": 1, "deny_paths": [{"path": "private", "deny": ["read"]}]})
    )
    original = Path.lstat

    def guarded(path, *args, **kwargs):
        assert path != denied and denied not in path.parents
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", guarded)
    with pytest.raises(InvalidInput, match="vault_scope_protected"):
        vault_scope.effective(denied, {"include": [""], "exclude": []})
