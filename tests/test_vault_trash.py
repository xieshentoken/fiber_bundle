import json
import shutil

import pytest

from fiber_bundle import auth, database, vault_catalog, vault_trash
from fiber_bundle.config import InvalidInput

CHECKED_HELPER = vault_trash.checked_helper


@pytest.fixture(autouse=True)
def helper_fixture(monkeypatch, tmp_path):
    # System moves are injected in these unit tests; real helper proof is separate.
    monkeypatch.setattr(vault_trash, "checked_helper", lambda: tmp_path / "synthetic-helper")


def attached(config, db, tmp_path):
    path = tmp_path / "disposable"
    path.mkdir()
    (path / "note.md").write_text("# synthetic")
    p = vault_catalog.preview(
        config, db, name="disposable", root=str(path), scope={"include": [""], "exclude": []}
    )
    vid = vault_catalog.attach(config, db, p["preview_id"])["vault_id"]
    cid = auth.create_client(db, "synthetic", False)["id"]
    auth.set_grant(db, cid, vid, query=True, original=False, add=False)
    return path, vid, cid


def test_trash_exact_review_and_recovery_does_not_restore_grants(
    config, store, tmp_path, monkeypatch
):
    db = database.connect(config.state_root)
    path, vid, cid = attached(config, db, tmp_path)
    p = vault_trash.preview(config, db, vid)
    with pytest.raises(InvalidInput):
        vault_trash.execute(config, db, p["id"], confirmed_root="/wrong")
    destination = tmp_path / "system-trash-fixture"
    monkeypatch.setattr(vault_trash, "move", lambda source: str(shutil.move(source, destination)))
    assert vault_trash.execute(config, db, p["id"], confirmed_root=str(path))["state"] == "trashed"
    with pytest.raises(auth.Denied):
        auth.grant(db, cid, vid, "query")
    assert not path.exists() and (destination / "note.md").exists()
    with pytest.raises(InvalidInput):
        vault_trash.execute(config, db, p["id"], confirmed_root=str(path))
    shutil.move(destination, path)
    p = vault_catalog.preview(
        config, db, name="disposable", root=str(path), scope={"include": [""], "exclude": []}
    )
    assert vault_catalog.attach(config, db, p["preview_id"])["vault_id"] == vid
    with pytest.raises(auth.Denied):
        auth.grant(db, cid, vid, "query")
    db.close()


def test_unknown_move_is_not_retried_and_permissions_stay_revoked(
    config, store, tmp_path, monkeypatch
):
    db = database.connect(config.state_root)
    path, vid, cid = attached(config, db, tmp_path)
    p = vault_trash.preview(config, db, vid)
    calls = []

    def ambiguous(source):
        calls.append(str(source))
        raise TimeoutError()

    monkeypatch.setattr(vault_trash, "move", ambiguous)
    with pytest.raises(InvalidInput, match="reconciliation"):
        vault_trash.execute(config, db, p["id"], confirmed_root=str(path))
    assert len(calls) == 1
    assert vault_trash.reconcile(config, db, p["id"])["same_directory"]
    with pytest.raises(InvalidInput):
        vault_trash.execute(config, db, p["id"], confirmed_root=str(path))
    assert len(calls) == 1
    with pytest.raises(InvalidInput):
        vault_trash.confirm_recovery(config, db, p["id"], "/wrong")
    assert vault_trash.confirm_recovery(config, db, p["id"], str(path))["state"] == "detached"
    with pytest.raises(auth.Denied):
        auth.grant(db, cid, vid, "query")
    db.close()


def test_protected_scope_refuses_trash_before_recursive_read(config, store, tmp_path, monkeypatch):
    db = database.connect(config.state_root)
    path, vid, _ = attached(config, db, tmp_path)
    (path / ".knowledge-organizer-policy.json").write_text(
        json.dumps({"schema_version": 1, "deny_paths": [{"path": "private", "deny": ["read"]}]})
    )
    from fiber_bundle.backup_inventory import Inventory

    def forbidden(*args, **kwargs):
        pytest.fail("protected directory inventory reached")

    monkeypatch.setattr(Inventory, "tree", forbidden)
    with pytest.raises(InvalidInput, match="protected"):
        vault_trash.preview(config, db, vid)
    db.close()


def test_changed_preview_refuses_system_move(config, store, tmp_path, monkeypatch):
    db = database.connect(config.state_root)
    path, vid, _ = attached(config, db, tmp_path)
    p = vault_trash.preview(config, db, vid)
    (path / "note.md").write_text("# changed")
    monkeypatch.setattr(vault_trash, "move", lambda *args: pytest.fail("changed directory moved"))
    with pytest.raises(InvalidInput, match="preview_changed"):
        vault_trash.execute(config, db, p["id"], confirmed_root=str(path))
    db.close()


def test_missing_helper_is_preflight_failure_without_revoking_access(
    config, store, tmp_path, monkeypatch
):
    db = database.connect(config.state_root)
    path, vid, cid = attached(config, db, tmp_path)
    intent = vault_trash.preview(config, db, vid)
    monkeypatch.setattr(vault_trash, "checked_helper", CHECKED_HELPER)
    monkeypatch.delenv("FIBER_TRASH_HELPER", raising=False)
    with pytest.raises(InvalidInput, match="helper_required"):
        vault_trash.execute(config, db, intent["id"], confirmed_root=str(path))
    assert path.is_dir()
    assert auth.grant(db, cid, vid, "query")
    assert (
        db.execute("SELECT state FROM vault_operations WHERE id=?", (intent["id"],)).fetchone()[0]
        == "prepared"
    )
    db.close()
