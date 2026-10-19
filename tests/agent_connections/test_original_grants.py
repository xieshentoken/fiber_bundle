"""Managed originals require every explicit v2 authorization; add remains forbidden."""

import pytest

from fiber_bundle import auth, database
from fiber_bundle.agent_connections import application, configuration, store
from fiber_bundle.agent_connections.contracts import ConnectionSpec

pytest_plugins = ["test_source_imports", "test_execution"]


@pytest.mark.parametrize(
    "schema,originals,state,grant,allowed",
    [
        ("v1", False, "enabled", True, False),
        ("v2", False, "enabled", True, False),
        ("v2", True, "enabled", True, True),
        ("v2", True, "enabled", False, False),
        ("v2", True, "paused", True, False),
        ("v2", True, "revoked", True, False),
    ],
)
def test_managed_original_permission_matrix(
    config, owner, schema, originals, state, grant, allowed
):
    _, _, ids = owner
    vid = ids["personal"]
    spec = ConnectionSpec(
        schema_version="fiber-agent-connection/" + schema,
        name="原件授权验证",
        agent_type="mcp",
        vault_ids=[vid],
        allow_originals=originals,
    )
    with database.connect(config.state_root) as db:
        item = configuration.create(config, db, spec, "create")
        application.apply(config, db, store.get(db, item["id"]), "http://127.0.0.1:18849")
        configuration.control(config, db, item["id"], "enable", 1, "enable")
        db.execute("UPDATE agent_connections SET state=? WHERE id=?", (state, item["id"]))
        auth.set_grant(db, item["client_id"], vid, query=True, original=grant, add=True)
        if allowed:
            assert auth.grant(db, item["client_id"], vid, "original")["id"] == vid
        else:
            with pytest.raises(auth.Denied):
                auth.grant(db, item["client_id"], vid, "original")
        with pytest.raises(auth.Denied):
            auth.grant(db, item["client_id"], vid, "add")
        auth.set_grant(db, item["client_id"], ids["supplier"], query=True, original=True, add=True)
        with pytest.raises(auth.Denied):
            auth.grant(db, item["client_id"], ids["supplier"], "original")
