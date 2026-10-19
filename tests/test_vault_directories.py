"""Observable directory discovery boundaries using synthetic, isolated Vaults."""

import json
from pathlib import Path

import pytest

from fiber_bundle import cloud_files, database, vault_scope
from fiber_bundle import vault_directories as directories
from fiber_bundle.config import IntegrityError
from fiber_bundle.vault_save_contract import SettingsError


def scope(root, include=None, exclude=None):
    return vault_scope.effective(root, {"include": include or [""], "exclude": exclude or []})


def test_discovery_is_shallow_and_never_opens_notes_or_denied_metadata(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    for name in ("Inbox", "Archive", "images", "secret", ".obsidian", "trash", "cloud.icloud"):
        (root / name).mkdir()
    (root / "Inbox/nested").mkdir()
    (root / "Inbox/private-note.md").write_text("SYNTHETIC BODY MUST NOT BE READ")
    (root / "linked").symlink_to(root / "Inbox", target_is_directory=True)
    (root / vault_scope.POLICY).write_text(
        json.dumps({"schema_version": 1, "deny_paths": [{"path": "secret", "deny": ["read"]}]})
    )
    inspected = []
    real_available = cloud_files.require_available

    def available(path, **kwargs):
        assert not kwargs.get("tree")
        assert path.name not in {"secret", ".obsidian", "trash", "cloud.icloud", "linked"}
        inspected.append(path)
        return real_available(path, **kwargs)

    real_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path.suffix != ".md"
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(cloud_files, "require_available", available)
    monkeypatch.setattr(Path, "open", guarded_open)
    result = directories.scan(root, scope(root))
    assert [d["name"] for d in result["directories"]] == ["Archive", "Inbox", "images"]
    assert root / "Inbox/nested" not in inspected
    result = directories.scan(root, scope(root), relative_parent="Inbox")
    assert [d["relative_path"] for d in result["directories"]] == ["Inbox/nested"]


def test_nested_policy_and_include_ancestors(tmp_path):
    root = tmp_path
    (root / "parent/allowed").mkdir(parents=True)
    (root / "parent/denied").mkdir()
    (root / "parent" / vault_scope.POLICY).write_text(
        json.dumps({"schema_version": 1, "deny_paths": [{"path": "denied", "deny": ["verify"]}]})
    )
    limited = scope(root, ["parent/allowed"])
    parent = directories.scan(root, limited)["directories"][0]
    assert parent["name"] == "parent" and parent["selectable"] is False
    children = directories.scan(root, scope(root), relative_parent="parent")["directories"]
    assert [d["name"] for d in children] == ["allowed"]
    with pytest.raises(SettingsError, match="scope_denied"):
        directories.scan(root, scope(root), relative_parent="parent/denied")


@pytest.mark.parametrize("parent", ["../outside", "/tmp", "Inbox/**", ".git", "trash", "a.icloud"])
def test_explicit_parent_cannot_bypass_filters(tmp_path, parent):
    with pytest.raises(SettingsError):
        directories.scan(tmp_path, scope(tmp_path), relative_parent=parent)


def test_paginated_metadata_cursor_rejects_changed_listing(tmp_path):
    for index in range(105):
        (tmp_path / f"folder-{index:03}").mkdir()
    first = directories.scan(tmp_path, scope(tmp_path))
    assert len(first["directories"]) == 100 and first["next_cursor"]
    second = directories.scan(tmp_path, scope(tmp_path), cursor=first["next_cursor"])
    assert len(second["directories"]) == 5 and second["next_cursor"] is None
    assert not {d["name"] for d in first["directories"]} & {
        d["name"] for d in second["directories"]
    }
    (tmp_path / "new").mkdir()
    with pytest.raises(SettingsError, match="stale_cursor"):
        directories.scan(tmp_path, scope(tmp_path), cursor=first["next_cursor"])


def test_directory_scan_is_bounded_and_cloud_placeholders_are_skipped(tmp_path, monkeypatch):
    for index in range(8):
        (tmp_path / f"folder-{index}").mkdir()
    monkeypatch.setattr(directories, "SCAN_LIMIT", 3)
    result = directories.scan(tmp_path, scope(tmp_path))
    assert result["truncated"] and len(result["directories"]) == 3
    assert result["next_cursor"]
    all_paths = [d["relative_path"] for d in result["directories"]]
    cursor = result["next_cursor"]
    while cursor:
        page = directories.scan(tmp_path, scope(tmp_path), cursor=cursor)
        all_paths.extend(d["relative_path"] for d in page["directories"])
        cursor = page["next_cursor"]
    assert len(all_paths) == len(set(all_paths)) == 8
    original = cloud_files.require_available

    def placeholder(path, **kwargs):
        if path.name == "folder-0":
            raise IntegrityError("cloud_item_not_downloaded")
        return original(path, **kwargs)

    monkeypatch.setattr(directories, "SCAN_LIMIT", 5000)
    monkeypatch.setattr(cloud_files, "require_available", placeholder)
    assert "folder-0" not in {
        d["name"] for d in directories.scan(tmp_path, scope(tmp_path))["directories"]
    }


def test_replacing_parent_with_symlink_cannot_redirect_listing(tmp_path, monkeypatch):
    from fiber_bundle import file_exchange

    root = tmp_path / "vault"
    (root / "Inbox").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private-directory").mkdir()
    real = file_exchange.parent_fd

    def swapped(path, relative):
        (root / "Inbox").rename(root / "previous")
        (root / "Inbox").symlink_to(outside, target_is_directory=True)
        return real(path, relative)

    monkeypatch.setattr(file_exchange, "parent_fd", swapped)
    with pytest.raises(OSError):
        directories.scan(root, scope(root), relative_parent="Inbox")


def test_discovery_token_does_not_admit_or_index_and_is_bound_to_root(config, store, tmp_path):
    root = tmp_path / "unregistered"
    root.mkdir()
    (root / "Inbox").mkdir()
    with database.connect(config.state_root) as db:
        before = [tuple(r) for r in db.execute("SELECT * FROM vaults")]
        result = directories.preview(
            config,
            db,
            root=str(root),
            scope={"include": [""], "exclude": []},
            preview_id=None,
            relative_parent="",
            cursor=None,
        )
        assert [tuple(r) for r in db.execute("SELECT * FROM vaults")] == before
        token = result["directory_preview_id"]
        result = directories.preview(
            config,
            db,
            root=None,
            scope={"include": [""], "exclude": []},
            preview_id=token,
            relative_parent="Inbox",
            cursor=None,
        )
        assert result["directories"] == []
        root.rename(root.with_name("old"))
        root.mkdir()
        with pytest.raises(SettingsError, match="directory_changed"):
            directories.preview(
                config, db, root=None, scope={}, preview_id=token, relative_parent="", cursor=None
            )
        db.execute("UPDATE vault_directory_previews SET expires_at=0")
        with pytest.raises(SettingsError, match="preview_expired"):
            directories.load(db, token, "discovery")


def test_deep_target_is_browsable_and_valid_save_path(tmp_path):
    from fiber_bundle.vault_save_contract import directory

    relative = "/".join(f"level-{n}" for n in range(20))
    (tmp_path / relative / "chosen").mkdir(parents=True)
    assert directory(relative + "/chosen") == relative + "/chosen"
    page = directories.scan(tmp_path, scope(tmp_path), relative_parent=relative)
    assert [n["relative_path"] for n in page["directories"]] == [relative + "/chosen"]


def test_budgeted_pages_continue_past_files_and_filtered_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(directories, "SCAN_LIMIT", 3)
    for n in range(12):
        (tmp_path / f"file-{n}").touch()
    for n in range(9):
        (tmp_path / f"directory-{n}").mkdir()
    (tmp_path / ".private").mkdir()
    cursor, found, pages = None, [], 0
    while True:
        page = directories.scan(tmp_path, scope(tmp_path), cursor=cursor)
        pages += 1
        found.extend(n["relative_path"] for n in page["directories"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert pages < 20
    assert set(found) == {f"directory-{n}" for n in range(9)}
    assert len(found) == 9
