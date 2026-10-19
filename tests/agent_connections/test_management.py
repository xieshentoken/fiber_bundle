import json

import pytest
from schema_support import remove_agent_schema

from fiber_bundle import auth, database
from fiber_bundle.agent_connections import configuration, files, store
from fiber_bundle.agent_connections.contracts import ConnectionSpec


@pytest.fixture
def connection(config, store):
    db = database.connect(config.state_root)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    value = ConnectionSpec(name="Synthetic MCP", agent_type="mcp", vault_ids=[vid])
    result = configuration.create(config, db, value, "create-one")
    yield db, value, result
    db.close()


def test_create_paused_private_idempotent_and_no_secret_readback(config, connection):
    db, value, result = connection
    assert result["access_state"] == "paused"
    assert result == configuration.create(config, db, value, "create-one")
    assert db.execute("SELECT count(*) FROM clients").fetchone()[0] == 1
    token = files.credential(config, result["id"])
    assert token not in json.dumps(result)
    assert token.encode() not in (config.state_root / "metadata.sqlite3").read_bytes()
    assert (
        files.folder(config, result["id"], secret=True) / "credential.json"
    ).stat().st_mode & 0o777 == 0o600
    assert not auth.client(db, "Bearer " + token)["create_vault"]
    with pytest.raises(auth.Denied):
        auth.grant(db, result["client_id"], value.vault_ids[0], "query")
    with pytest.raises(store.Conflict):
        configuration.create(
            config, db, value.model_copy(update={"name": "different"}), "create-one"
        )


def applied(db, result):
    db.execute(
        "UPDATE agent_connections SET applied_json=? WHERE id=?",
        (
            json.dumps(
                {
                    "revision": result["revision"],
                    "credential_generation": result["credential_generation"],
                }
            ),
            result["id"],
        ),
    )


def test_enable_pause_cas_revoke_and_rotate_old_token(config, connection):
    db, value, result = connection
    cid, identity = result["client_id"], result["id"]
    token = files.credential(config, identity)
    applied(db, result)
    enabled = configuration.control(config, db, identity, "enable", 1, "enable-one")
    assert enabled["access_state"] == "enabled"
    auth.grant(db, cid, value.vault_ids[0], "query")
    with pytest.raises(auth.Denied):
        auth.grant(db, cid, value.vault_ids[0], "original")
    with pytest.raises(store.Conflict):
        configuration.control(config, db, identity, "pause", 1, "pause-conflict")
    paused = configuration.control(config, db, identity, "pause", 2, "pause-one")
    assert paused["access_state"] == "paused"
    with pytest.raises(auth.Denied):
        auth.grant(db, cid, value.vault_ids[0], "query")
    op = configuration.rotate(config, db, identity, 3, "rotate-one")
    assert op["state"] == "succeeded"
    with pytest.raises(auth.Denied):
        auth.client(db, "Bearer " + token)
    current = store.public(config, db, store.get(db, identity))
    assert current["credential_generation"] == 2 and current["access_state"] == "paused"
    configuration.control(config, db, identity, "revoke", 4, "revoke-one")
    with pytest.raises(store.Conflict):
        configuration.control(config, db, identity, "enable", 5, "no-resume")


def test_manual_permissions_epoch_change_does_not_auto_restore(config, connection):
    db, value, result = connection
    applied(db, result)
    auth.set_grant(
        db, result["client_id"], value.vault_ids[0], query=True, original=False, add=False
    )
    auth.set_grant(
        db, result["client_id"], value.vault_ids[0], query=False, original=False, add=False
    )
    with pytest.raises(store.Conflict, match="permissions_changed"):
        configuration.control(config, db, result["id"], "enable", 1, "enable-one")


def test_interrupted_create_preserves_intent_and_never_auto_replays(config, store, monkeypatch):
    db = database.connect(config.state_root)
    vid = db.execute("SELECT id FROM vaults LIMIT 1").fetchone()[0]
    value = ConnectionSpec(name="Synthetic", agent_type="mcp", vault_ids=[vid])
    monkeypatch.setattr(
        files, "put", lambda *a, **kw: (_ for _ in ()).throw(OSError("synthetic crash"))
    )
    with pytest.raises(OSError):
        configuration.create(config, db, value, "crash-one")
    row = db.execute("SELECT * FROM agent_connections").fetchone()
    assert row["state"] == "repair_required"
    assert not db.execute("SELECT can_query FROM client_vault_grants").fetchone()[0]
    db.close()
    store_module = __import__("fiber_bundle.agent_connections.store", fromlist=["recover"])
    store_module.recover(config)
    db = database.connect(config.state_root)
    assert db.execute("SELECT state FROM agent_connection_operations").fetchone()[0] == "reconcile"
    configuration.create(config, db, value, "crash-one")
    assert db.execute("SELECT count(*) FROM clients").fetchone()[0] == 1
    db.close()


def test_schema26_migration_preserves_all_prior_rows(config, store):
    db = database.connect(config.state_root)
    remove_agent_schema(db)
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    before = {t: [tuple(r) for r in db.execute('SELECT * FROM "' + t + '"')] for t in tables}
    db.execute("PRAGMA user_version=26")
    db.close()
    database.migrate(config)
    db = database.connect(config.state_root)
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    for t, rows in before.items():
        assert [tuple(r) for r in db.execute('SELECT * FROM "' + t + '"')] == rows
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db.close()


@pytest.mark.parametrize("change", ["permissions", "symlink", "hardlink"])
def test_private_credential_refuses_unsafe_replacements(config, connection, change):
    import os

    from fiber_bundle.config import InvalidInput

    _, _, result = connection
    path = files.folder(config, result["id"], secret=True) / "credential.json"
    if change == "permissions":
        path.chmod(0o644)
    elif change == "symlink":
        other = path.with_name("other.json")
        path.rename(other)
        path.symlink_to(other)
    else:
        os.link(path, path.with_name("other.json"))
    with pytest.raises((InvalidInput, OSError)):
        files.credential(config, result["id"])


def test_cancel_intent_is_idempotent_and_bound_to_target(connection):
    db, _, result = connection
    first = store.operation(db, result["id"], "probe", "probe-one", {})
    second = store.operation(db, result["id"], "probe", "probe-two", {})
    cancelled = configuration.cancel(db, result["id"], first["id"], 1, "cancel-one")
    assert cancelled["cancel_requested"]
    assert configuration.cancel(db, result["id"], first["id"], 1, "cancel-one") == cancelled
    assert (
        db.execute(
            "SELECT count(*) FROM agent_connection_operations WHERE kind='cancel'"
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(store.Conflict, match="request_conflict"):
        configuration.cancel(db, result["id"], second["id"], 1, "cancel-one")
    with pytest.raises(store.Conflict):
        configuration.cancel(db, result["id"], second["id"], 2, "cancel-two")
    assert not store.op(db, result["id"], second["id"])["cancel_requested"]
