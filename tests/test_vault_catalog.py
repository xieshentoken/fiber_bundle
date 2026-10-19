import json
import os
import shutil
from pathlib import Path

import pytest
from schema_support import remove_agent_schema

from fiber_bundle import auth, database, note_inventory, vault_catalog, vault_scope, vaults
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.note_index import Limits, snapshot
from fiber_bundle.note_search_cache import fingerprint


def prepare(config, db, tmp_path, **kwargs):
    root = tmp_path / "research"
    root.mkdir(exist_ok=True)
    value = vault_catalog.preview(
        config, db, name="研究库", root=str(root), scope={"include": [""], "exclude": []}, **kwargs
    )
    return root, value


def test_catalog_preserves_bootstrap_and_server_enforces_read_only(config, store, tmp_path):
    db = database.connect(config.state_root)
    before = [tuple(r) for r in db.execute("SELECT id,name,relative_path FROM vaults ORDER BY id")]
    root, preview = prepare(config, db, tmp_path)
    (root / "a.md").write_text("# 中文笔记\n合成压力 2.4 MPa")
    preview = vault_catalog.preview(
        config, db, name="研究库", root=str(root), scope={"include": [""], "exclude": []}
    )
    result = vault_catalog.attach(config, db, preview["preview_id"])
    assert [
        tuple(r)
        for r in db.execute(
            "SELECT id,name,relative_path FROM vaults WHERE root_kind='legacy' ORDER BY id"
        )
    ] == before
    cid = auth.create_client(db, "reader", False)["id"]
    vid = result["vault_id"]
    auth.set_grant(db, cid, vid, query=True, original=True, add=False)
    with pytest.raises(auth.Denied):
        auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    row = auth.grant(db, cid, vid, "query")
    assert vaults.checked_vault(config, row) == root
    assert note_inventory.read(config, db, row).notes[0].title == "中文笔记"
    with pytest.raises(InvalidInput, match="overlaps_vault"):
        vault_catalog.preview(
            config, db, name="nested", root=str(root), scope={"include": [""], "exclude": []}
        )
    old = (root / "a.md").read_bytes()
    assert vault_catalog.unlink(config, db, vid)["files_modified"] is False
    assert (root / "a.md").read_bytes() == old
    assert vaults.client_vaults(db, cid) == []
    with pytest.raises(auth.Denied):
        auth.grant(db, cid, vid, "query")
    with pytest.raises(auth.Denied):
        vaults.set_default_query(db, vid, True)
    db.close()


def test_preview_rechecks_policy_directory_and_expiry(config, store, tmp_path):
    db = database.connect(config.state_root)
    root, value = prepare(config, db, tmp_path)
    (root / vault_scope.POLICY).write_text(
        json.dumps({"schema_version": 1, "deny_paths": [{"path": "private", "deny": ["read"]}]})
    )
    with pytest.raises(InvalidInput, match="preview_changed"):
        vault_catalog.attach(config, db, value["preview_id"])
    (root / vault_scope.POLICY).unlink()
    shutil.move(root, root.with_name("old"))
    root.mkdir()
    with pytest.raises(InvalidInput, match="preview_changed"):
        vault_catalog.attach(config, db, value["preview_id"])
    db.execute("UPDATE vault_admissions SET expires_at=0")
    with pytest.raises(InvalidInput, match="preview_expired"):
        vault_catalog.attach(config, db, value["preview_id"])
    db.close()


def test_denied_children_never_stat_read_or_count(monkeypatch, tmp_path):
    root = tmp_path / "scope"
    root.mkdir()
    (root / "allowed").mkdir()
    (root / "allowed/a.md").write_text("# public")
    denied = root / "secret"
    denied.mkdir()
    (denied / "x.md").write_text("PRIVATE")
    (root / vault_scope.POLICY).write_text(
        json.dumps(
            {"schema_version": 1, "deny_paths": [{"path": "secret", "deny": ["read", "index"]}]}
        )
    )
    scope = vault_scope.effective(root, {"include": [""], "exclude": []})
    real = Path.lstat

    def guarded(path, *args, **kwargs):
        assert path != denied and denied not in path.parents
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", guarded)
    assert [n.title for n in snapshot(root, set(), set(), Limits(entries=3), scope=scope)] == [
        "public"
    ]
    assert len(list(vault_scope.entries(root, scope, maximum=2))) == 2
    with pytest.raises(InvalidInput, match="protected"):
        vault_scope.effective(denied, {"include": [""], "exclude": []})


def test_scope_and_policy_change_invalidate_fingerprint(config, store, tmp_path):
    db = database.connect(config.state_root)
    root, p = prepare(config, db, tmp_path)
    (root / "x.md").write_text("# synthetic")
    p = vault_catalog.preview(
        config, db, name="研究库", root=str(root), scope={"include": [""], "exclude": []}
    )
    vid = vault_catalog.attach(config, db, p["preview_id"])["vault_id"]
    row = db.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone()
    before = fingerprint(config, db, row)
    (root / vault_scope.POLICY).write_text(
        json.dumps({"schema_version": 1, "deny_paths": [{"path": "x.md", "deny": ["index"]}]})
    )
    assert fingerprint(config, db, row) != before
    assert note_inventory.read(config, db, row).notes == []
    db.close()


def test_replaced_directory_and_symlink_rejected(config, store, tmp_path):
    db = database.connect(config.state_root)
    root, p = prepare(config, db, tmp_path)
    vid = vault_catalog.attach(config, db, p["preview_id"])["vault_id"]
    row = db.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone()
    os.rename(root, root.with_name("old"))
    root.mkdir()
    with pytest.raises(IntegrityError, match="identity changed"):
        vaults.checked_vault(config, row)
    root.rmdir()
    root.symlink_to(root.with_name("old"), target_is_directory=True)
    with pytest.raises(IntegrityError):
        vaults.checked_vault(config, row)
    db.close()


def test_admin_routes_and_confirmation_replay(http, tmp_path):
    root = tmp_path / "catalog"
    root.mkdir()
    payload = {"name": "third", "root": str(root)}
    response = http.post("/admin/vault-catalog/preview", json=payload)
    assert response.status_code == 200
    assert response.json()["read_only"] is True
    preview_id = response.json()["preview_id"]
    result = http.post("/admin/vault-catalog/attach", json={"preview_id": preview_id})
    assert result.status_code == 200
    assert (
        http.post("/admin/vault-catalog/attach", json={"preview_id": preview_id}).status_code == 422
    )
    assert (
        http.post("/admin/vault-catalog/" + result.json()["vault_id"] + "/unlink").status_code
        == 200
    )


def test_schema25_upgrade_preserves_existing_identity_grants_and_backup(config, store):
    db = database.connect(config.state_root)
    cid = auth.create_client(db, "legacy", False)["id"]
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    before = [tuple(r) for r in db.execute("SELECT * FROM client_vault_grants")]
    remove_agent_schema(db)
    for column in ("absolute_root", "root_kind", "read_only", "attachment_state", "scope_json"):
        db.execute("ALTER TABLE vaults DROP COLUMN " + column)
    db.execute("DROP TABLE vault_admissions")
    db.execute("DROP TABLE vault_operations")
    db.execute("PRAGMA user_version=25")
    db.close()
    with pytest.raises(IntegrityError, match="explicit migration"):
        database.connect(config.state_root)
    backup = database.migrate(config)
    assert backup is not None
    import sqlite3

    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 25
    db = database.connect(config.state_root)
    assert [tuple(r) for r in db.execute("SELECT * FROM client_vault_grants")] == before
    assert auth.grant(db, cid, vid, "add")["root_kind"] == "legacy"
    assert database.migrate(config) is None
    db.close()


def test_catalog_encrypted_backup_restore_is_scoped_and_rebound(config, store, tmp_path):
    from fiber_bundle import backup_bundle, backup_restore
    from fiber_bundle.config import load_config

    db = database.connect(config.state_root)
    root, p = prepare(config, db, tmp_path)
    (root / "a.md").write_text("# safe")
    (root / "private").mkdir()
    (root / "private/secret.md").write_text("excluded sentinel")
    (root / vault_scope.POLICY).write_text(
        json.dumps({"schema_version": 1, "deny_paths": [{"path": "private", "deny": ["read"]}]})
    )
    # Policy was added after preview, so explicitly preview the new reviewed scope.
    p = vault_catalog.preview(
        config, db, name="研究库", root=str(root), scope={"include": [""], "exclude": []}
    )
    vid = vault_catalog.attach(config, db, p["preview_id"])["vault_id"]
    db.close()
    password = "synthetic-backup-password"
    backup_bundle.create(config, tmp_path / "backup", password)
    backup_restore.restore(tmp_path / "backup", tmp_path / "restored", password)
    target = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    backup_restore.activate(target, source_stopped=True)
    restored_db = database.connect(target.state_root)
    row = restored_db.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone()
    rebound = vaults.checked_vault(target, row)
    assert rebound != root and rebound.is_relative_to(target.project_root)
    assert (rebound / "a.md").read_text() == "# safe"
    assert not (rebound / "private").exists()
    restored_db.close()
