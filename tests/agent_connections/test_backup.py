"""Full encrypted restore includes public connection state, excludes secrets and pauses access."""

from fiber_bundle import auth, backup_bundle, backup_restore, database
from fiber_bundle.agent_connections import (
    application,
    configuration,
    files,
)
from fiber_bundle.agent_connections import (
    store as connection_store,
)
from fiber_bundle.agent_connections.contracts import ConnectionSpec
from fiber_bundle.config import load_config


def test_agent_backup_restore_requires_rotation_before_reenable(config, store, tmp_path):
    db = database.connect(config.state_root)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    value = ConnectionSpec(name="Synthetic backed-up MCP", agent_type="mcp", vault_ids=[vid])
    row = configuration.create(config, db, value, "create")
    application.apply(config, db, connection_store.get(db, row["id"]), "http://127.0.0.1:8765")
    row = configuration.control(config, db, row["id"], "enable", 1, "enable")
    token = files.credential(config, row["id"])
    db.close()
    backup_bundle.create(config, tmp_path / "bundle", "synthetic-backup-password")
    result = backup_restore.restore(
        tmp_path / "bundle", tmp_path / "restored", "synthetic-backup-password"
    )
    assert result["state"] == "restored_locked" and not result["automatic_start"]
    target = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    assert not files.folder(target, row["id"], secret=True).exists()
    assert (files.folder(target, row["id"]) / "transport.json").is_file()
    backup_restore.activate(target, source_stopped=True)
    db = database.connect(target.state_root)
    try:
        restored = connection_store.public(target, db, connection_store.get(db, row["id"]))
        assert restored["access_state"] == "repair_required"
        assert (
            restored["configuration_state"] == "repair_required"
            and not connection_store.context(target, db, row["id"])["enabled"]
        )
        assert restored["revision"] > row["revision"]
        try:
            auth.client(db, "Bearer " + token)
        except auth.Denied:
            pass
        else:
            raise AssertionError("restored credential must be inactive")
        rotated = configuration.rotate(target, db, row["id"], restored["revision"], "rotate")
        assert rotated["state"] == "succeeded"
        applied = application.apply(
            target, db, connection_store.get(db, row["id"]), "http://127.0.0.1:8765"
        )
        assert (
            applied["state"] == "applied"
            and not connection_store.context(target, db, row["id"])["enabled"]
        )
    finally:
        db.close()


def test_backup_refuses_pending_agent_file_writes(config, store, tmp_path):
    import pytest

    from fiber_bundle.config import InvalidInput

    db = database.connect(config.state_root)
    vid = db.execute("SELECT id FROM vaults LIMIT 1").fetchone()[0]
    row = configuration.create(
        config, db, ConnectionSpec(name="Pending MCP", agent_type="mcp", vault_ids=[vid]), "create"
    )
    connection_store.operation(db, row["id"], "probe", "pending", {})
    db.close()
    with pytest.raises(InvalidInput, match="backup_agent_operation_busy"):
        backup_bundle.create(config, tmp_path / "bundle", "synthetic-password")
