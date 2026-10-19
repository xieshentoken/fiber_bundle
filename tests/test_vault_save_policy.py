"""Draft settings CAS, directory journal, authorization and preservation contracts."""

import json
import os
import sqlite3
from uuid import uuid4

import pytest
from pydantic import ValidationError

from fiber_bundle import auth, database, moc_store, vault_catalog, vault_directories, vault_scope
from fiber_bundle import vault_save_policy as policy
from fiber_bundle.config import IntegrityError, InvalidInput, load_config
from fiber_bundle.vault_save_contract import SaveSettings, SettingsError


@pytest.fixture
def vault(config, store, tmp_path):
    root = tmp_path / "layout-vault"
    root.mkdir()
    db = database.connect(config.state_root)
    value = vault_catalog.preview(
        config,
        db,
        name="layout",
        root=str(root),
        read_only=False,
        scope={"include": [""], "exclude": []},
    )
    identity = vault_catalog.attach(config, db, value["preview_id"])["vault_id"]
    yield root, identity, db
    db.close()


def proposed(config, vault, **changes):
    _, identity, db = vault
    settings = SaveSettings(layout_mode="obsidian_folders", **changes)
    return policy.preview(
        config, db, identity, settings, policy.get(config, db, identity)["revision"]
    )


def confirm(config, vault, preview):
    _, identity, db = vault
    return policy.save(config, db, identity, preview["preview_id"], preview["expected_revision"])


def test_defaults_preview_and_explicit_save_do_not_publish_or_move_notes(config, vault):
    root, identity, db = vault
    (root / "original.md").write_bytes(b"# Synthetic original\n![[existing.png]]")
    before = (root / "original.md").read_bytes()
    state = policy.get(config, db, identity)
    assert state["revision"] == 0 and state["effective_mode"] == "legacy_bundle"
    preview = proposed(config, vault, inbox_dir="Inbox/raw", archive_dir="Archive/notes")
    assert preview["paths"]["expert_package"].startswith("Inbox/raw/")
    assert not (root / "Inbox").exists()
    assert preview["create_directories"] == [
        "Archive",
        "Archive/notes",
        "Inbox",
        "Inbox/raw",
        "images",
    ]
    saved = confirm(config, vault, preview)
    assert saved["revision"] == 1 and saved["state"] == "draft"
    assert saved["binding_current"] and saved["publication_ready"] is False
    assert saved["effective_mode"] == "legacy_bundle"
    assert (root / "Inbox/raw").is_dir() and (root / "Archive/notes").is_dir()
    assert (root / "original.md").read_bytes() == before
    assert not list(root.rglob("note.md"))
    assert (
        db.execute(
            "SELECT state FROM vault_operations WHERE id=?", (saved["operation_id"],)
        ).fetchone()[0]
        == "completed"
    )
    with pytest.raises(SettingsError, match="preview_expired"):
        confirm(config, vault, preview)


def test_revision_conflict_and_vault_isolation(config, vault):
    _, identity, db = vault
    first = proposed(config, vault)
    second = proposed(config, vault, archive_dir="OtherArchive")
    confirm(config, vault, first)
    with pytest.raises(SettingsError, match="revision_conflict"):
        confirm(config, vault, second)
    other = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
    assert policy.get(config, db, other)["revision"] == 0
    with pytest.raises(SettingsError, match="preview_changed"):
        policy.save(config, db, other, second["preview_id"], 0)
    assert policy.get(config, db, identity)["settings"]["archive_dir"] == "Archive"


@pytest.mark.parametrize(
    "path",
    [
        "/Inbox",
        "../Inbox",
        ".obsidian",
        "trash",
        "a.icloud",
        "a//b",
        "a/../b",
        "a\\b",
        "a\x00b",
        "Inbox/**",
    ],
)
def test_save_directory_contract_rejects_escape_and_internal_targets(path):
    with pytest.raises(ValidationError):
        SaveSettings(inbox_dir=path)


@pytest.mark.parametrize(
    "changes",
    [
        {"inbox_dir": "Archive"},
        {"image_dir": "Inbox/images"},
        {"image_dir": "Archive"},
        {"image_dir": "Inbox", "inbox_dir": "Inbox/notes"},
    ],
)
def test_overlapping_roles_are_rejected(config, vault, changes):
    with pytest.raises(SettingsError, match="directory_overlap"):
        proposed(config, vault, **changes)


def test_scope_and_root_changes_revoke_preview(config, vault):
    root, identity, db = vault
    preview = proposed(config, vault)
    (root / vault_scope.POLICY).write_text(
        json.dumps({"schema_version": 1, "deny_paths": [{"path": "Inbox", "deny": ["read"]}]})
    )
    with pytest.raises(SettingsError, match="scope_denied"):
        confirm(config, vault, preview)
    assert not (root / "Inbox").exists()
    (root / vault_scope.POLICY).unlink()
    root.rename(root.with_name("previous"))
    root.mkdir()
    with pytest.raises(SettingsError, match="directory_changed"):
        policy.get(config, db, identity)


def test_existing_directory_replacement_and_symlink_fail_before_writes(config, vault):
    root, _, _ = vault
    (root / "Inbox").mkdir()
    preview = proposed(config, vault)
    (root / "Inbox").rename(root / "old-Inbox")
    (root / "Inbox").mkdir()
    with pytest.raises(SettingsError, match="preview_changed"):
        confirm(config, vault, preview)
    assert not (root / "Archive").exists()
    (root / "Inbox").rmdir()
    (root / "Inbox").symlink_to(root / "old-Inbox", target_is_directory=True)
    with pytest.raises(InvalidInput, match="symlink paths are not allowed"):
        proposed(config, vault)


def test_case_alias_and_nested_policy_are_rejected(config, vault):
    root, identity, db = vault
    (root / "INBOX").mkdir()
    with pytest.raises(SettingsError, match="path_alias"):
        proposed(config, vault)
    (root / "INBOX").rmdir()
    (root / "parent").mkdir()
    (root / "parent" / vault_scope.POLICY).write_text(
        json.dumps({"schema_version": 1, "deny_paths": [{"path": "Inbox", "deny": ["index"]}]})
    )
    with pytest.raises(SettingsError, match="scope_denied"):
        proposed(config, vault, inbox_dir="parent/Inbox")
    preview = proposed(config, vault, inbox_dir="parent/Allowed")
    confirm(config, vault, preview)
    (root / "parent" / vault_scope.POLICY).write_text(
        json.dumps({"schema_version": 1, "deny_paths": [{"path": "Allowed", "deny": ["read"]}]})
    )
    assert policy.get(config, db, identity)["binding_current"] is False


def test_readonly_detached_and_bootstrap_identity_are_enforced(config, vault):
    _, identity, db = vault
    preview = proposed(config, vault)
    db.execute("UPDATE vaults SET read_only=1 WHERE id=?", (identity,))
    with pytest.raises(SettingsError, match="read_only"):
        confirm(config, vault, preview)
    db.execute("UPDATE vaults SET attachment_state='detached' WHERE id=?", (identity,))
    with pytest.raises(auth.Denied):
        policy.get(config, db, identity)
    db.execute("UPDATE vaults SET relative_path='untrusted' WHERE name='personal'")
    bootstrap = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    with pytest.raises(IntegrityError, match="identity changed"):
        vault_directories.vault_root(config, db, bootstrap)


def test_moc_destinations_use_stable_existing_ids_and_preserve_original_moc(config, vault):
    root, identity, db = vault
    mid = str(uuid4())
    metadata = moc_store.Record("Synthetic MOC", *vault_directories.key(root), 1, {})
    moc_store.save(config, identity, mid, str(uuid4()), metadata, "create", 0)
    assert policy.get(config, db, identity)["mocs"] == [{"id": mid, "title": "Synthetic MOC"}]
    preview = proposed(config, vault, moc_destinations={mid: "Areas/Research"}, archive_on_moc=True)
    confirm(config, vault, preview)
    assert (root / "Areas/Research").is_dir()
    assert moc_store.load(config, identity, mid) == metadata
    with pytest.raises(SettingsError, match="moc_unavailable"):
        proposed(config, vault, moc_destinations={str(uuid4()): "Areas/Research"})


def test_interrupted_creation_preserves_dirs_requires_explicit_resolution(
    config, vault, monkeypatch
):
    root, identity, db = vault
    preview = proposed(config, vault)
    real = os.mkdir

    def fail(path, *args, **kwargs):
        if path == "Inbox":
            raise OSError("synthetic mkdir interruption")
        return real(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "mkdir", fail)
        with pytest.raises(OSError):
            confirm(config, vault, preview)
    assert (root / "Archive").is_dir() and not (root / "Inbox").exists()
    state = policy.get(config, db, identity)
    assert state["revision"] == 0 and state["pending_operation"]["state"] == "reconcile"
    with pytest.raises(SettingsError, match="reconciliation_required"):
        proposed(config, vault)
    result = policy.abandon(config, db, identity, state["pending_operation"]["id"])
    assert result["directories_preserved"] and (root / "Archive").is_dir()
    with pytest.raises(SettingsError, match="preview_expired"):
        confirm(config, vault, preview)
    confirm(config, vault, proposed(config, vault))
    assert policy.get(config, db, identity)["revision"] == 1


def test_admin_routes_authentication_csrf_and_bounded_errors(http, config, tmp_path):
    root = tmp_path / "metadata-only"
    root.mkdir()
    (root / "Inbox").mkdir()
    url = "/admin/vault-catalog/directory-preview"
    response = http.post(url, json={"root": str(root)})
    assert response.status_code == 200
    assert response.json()["directories"][0]["relative_path"] == "Inbox"
    vid = http.get("/admin/vaults").json()[0]["id"]
    base = "/admin/vault-catalog/" + vid + "/save-policy"
    assert http.get(base).json()["effective_mode"] == "legacy_bundle"
    bad = http.post(base + "/preview", json={"expected_revision": 4, "settings": {}})
    assert bad.status_code == 409 and bad.json()["error"] == "save_policy_revision_conflict"
    http.headers.pop("X-CSRF-Token")
    assert http.post(url, json={"root": str(root)}).status_code == 403
    http.cookies.clear()
    assert http.get(base).status_code == 401
    assert http.post(url, json={"root": str(root)}).status_code == 401


def test_schema28_additive_migration_preserves_rows_and_requires_explicit_upgrade(config, store):
    from schema_support import remove_automatic_schema

    with database.connect(config.state_root) as db:
        remove_automatic_schema(db)
        db.execute("DROP TABLE vault_directory_previews")
        db.execute("DROP TABLE publication_locations")
        db.execute("DROP TABLE vault_save_policies")
        db.execute("PRAGMA user_version=28")
        before = {
            r[0]: [tuple(x) for x in db.execute('SELECT * FROM "' + r[0] + '"')]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    with pytest.raises(IntegrityError, match="explicit migration"):
        database.connect(config.state_root)
    backup = database.migrate(config)
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 28
    with database.connect(config.state_root) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        for name, rows in before.items():
            assert [tuple(x) for x in db.execute('SELECT * FROM "' + name + '"')] == rows
        assert db.execute("SELECT * FROM vault_save_policies").fetchall() == []


def test_http_save_roundtrip_requires_current_preview_and_explicit_preservation(http, config):
    vid = http.get("/admin/vaults").json()[0]["id"]
    base = "/admin/vault-catalog/" + vid + "/save-policy"
    response = http.post(
        base + "/preview",
        json={"expected_revision": 0, "settings": {"layout_mode": "obsidian_folders"}},
    )
    assert response.status_code == 200
    body = {"preview_id": response.json()["preview_id"], "expected_revision": 0}
    saved = http.put(base, json=body)
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert saved.json()["publication_ready"] is False
    assert http.put(base, json=body).status_code == 409
    bad = http.post(
        base + "/abandon", json={"operation_id": str(uuid4()), "preserve_directories": 1}
    )
    assert bad.status_code == 422


def test_encrypted_restore_keeps_draft_and_invalidates_directory_binding(config, vault, tmp_path):
    from fiber_bundle import backup_bundle, backup_restore

    root, identity, db = vault
    confirm(config, vault, proposed(config, vault))
    proposed(config, vault)
    backup_bundle.create(config, tmp_path / "backup", "synthetic-backup-password")
    backup_restore.restore(tmp_path / "backup", tmp_path / "restored", "synthetic-backup-password")
    restored = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    backup_restore.activate(restored, source_stopped=True)
    with database.connect(restored.state_root) as target:
        state = policy.get(restored, target, identity)
        assert state["revision"] == 1 and state["publication_ready"] is False
        assert state["binding_current"] is False
        assert target.execute("SELECT count(*) FROM vault_directory_previews").fetchone()[0] == 0
    assert (root / "Inbox").is_dir()
