"""Service controls preserve identities, old work and the other role's intent."""

import json
import time

import pytest
from test_message_channels import bind
from test_message_channels import channel as channel

from fiber_bundle.channels import store
from fiber_bundle.channels.services import policy
from fiber_bundle.config import InvalidInput


def make_intake(config, channel):
    entry, _ = channel
    current = bind(config, entry)
    with store.opened(config) as db:
        value = {**current["value"], "add": True}
        db.execute("UPDATE channels SET value=? WHERE id=?", (json.dumps(value), entry["id"]))
    return entry["id"]


def test_role_intents_are_independent_and_old_work_is_held(config, channel):
    identity = make_intake(config, channel)
    with store.readonly(config) as db:
        before = dict(db.execute("SELECT * FROM channels WHERE id=?", (identity,)).fetchone())
    t = time.time()
    query = policy.change(config, identity, "query", 0, enabled=True)
    intake = policy.change(config, identity, "intake", 0, enabled=True)
    assert intake["admit_after"] >= t and query["admit_after"] >= t
    policy.change(config, identity, "query", 1, enabled=False)
    assert policy.get(config, identity, "intake") == intake
    assert policy.get(config, identity, "query")["admit_after"] == query["admit_after"]
    assert policy.owned_channels(config) == {identity}
    with store.readonly(config) as db:
        assert (
            dict(db.execute("SELECT * FROM channels WHERE id=?", (identity,)).fetchone()) == before
        )


def test_revision_role_and_paused_transport_are_explicit(config, channel):
    identity = make_intake(config, channel)
    policy.change(config, identity, "intake", 0, enabled=True)
    with pytest.raises(InvalidInput, match="revision_conflict"):
        policy.change(config, identity, "intake", 0, enabled=False)
    with store.opened(config) as db:
        db.execute("UPDATE channels SET state='paused' WHERE id=?", (identity,))
        entry = store.get(db, identity)
    assert policy.intake_allowed(config, entry)
    policy.change(config, identity, "intake", 1, enabled=False)
    assert not policy.enabled(config, identity, "intake")
    assert policy.intake_allowed(config, entry)  # A claimed phase can finish safely.
    assert entry["revision"] == 1


def test_existing_query_bot_cannot_enable_intake(config, channel):
    entry, _ = channel
    bind(config, entry)
    with pytest.raises(InvalidInput, match="role_unavailable"):
        policy.change(config, entry["id"], "intake", 0, enabled=True)


def test_shared_identity_query_configuration_does_not_revoke_other_bot_save_permission(
    config, channel
):
    from fiber_bundle import auth, database

    entry, _ = channel
    current = bind(config, entry)
    with database.connect(config.state_root) as main:
        main.execute("UPDATE vaults SET read_only=0 WHERE id=?", (current["value"]["vault_id"],))
        auth.set_grant(
            main,
            current["value"]["client_id"],
            current["value"]["vault_id"],
            query=True,
            original=False,
            add=True,
        )
        with store.opened(config) as side:
            side.execute(
                "UPDATE channels SET value=? WHERE id=?",
                (json.dumps({**current["value"], "add": True}), entry["id"]),
            )
        other = store.save(
            config,
            main,
            {**current["value"], "name": "Independent query", "purpose": "query", "add": False},
            None,
            None,
        )
        assert not other["value"]["add"]
        assert auth.grant(main, current["value"]["client_id"], current["value"]["vault_id"], "add")


def test_receiver_account_lease_prevents_duplicate_listeners(config, channel):
    from fiber_bundle.channels.receiver_lease import owned

    entry, _ = channel
    current = bind(config, entry)
    with owned(config, current, "run"):
        with pytest.raises(InvalidInput, match="account_already_running"):
            with owned(config, current, "run"):
                pytest.fail("duplicate receiver")


def test_restore_disables_roles_without_replaying_history(config, channel):
    from fiber_bundle import restore_rebind

    identity = make_intake(config, channel)
    for role in policy.ROLES:
        policy.change(config, identity, role, 0, enabled=True)
    restore_rebind._quarantine_sidecars(config)
    for role in policy.ROLES:
        restored = policy.get(config, identity, role)
        assert restored["enabled"] is False and restored["revision"] == 2


def test_configuration_change_requires_its_workers_paused(config, channel):
    from fiber_bundle import database

    identity = make_intake(config, channel)
    policy.change(config, identity, "intake", 0, enabled=True)
    with store.opened(config) as side:
        side.execute("UPDATE channels SET state='paused' WHERE id=?", (identity,))
        current = store.get(side, identity)
    with database.connect(config.state_root) as main:
        with pytest.raises(InvalidInput, match="pause_processing_required"):
            store.save(config, main, current["value"], identity, current["revision"])
    assert policy.enabled(config, identity, "intake")
