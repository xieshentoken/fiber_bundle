"""Read-only reconciliation keeps intent history with one atomic durable outcome."""

import json
from unittest.mock import patch

import pytest
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_identities import track
from test_vault_sync_reconcile import changed, observe

from fiber_bundle import note_identity_store
from fiber_bundle.config import IntegrityError
from fiber_bundle.vault_sync import reconcile, scanner, store, working_intent


def test_unchanged_note_retains_same_frozen_intent_and_complete_state(config, identity_scope):
    client, vid, root = identity_scope
    observe(config, identity_scope)
    reconcile.current(config, client["id"], vid, "manual.md")
    with store.connection(config) as db:
        before = tuple(db.execute("SELECT * FROM working_versions").fetchone())
    reconcile.current(config, client["id"], vid, "manual.md")
    with store.connection(config) as db:
        assert tuple(db.execute("SELECT * FROM working_versions").fetchone()) == before
    assert before[-1] == "complete"
    assert (root / "manual.md").read_text().startswith("#")


def test_crash_before_read_intent_commit_rolls_back_prepared_row(config, identity_scope):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    original = scanner.signature
    calls = 0

    def crash(info):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic interruption before commit")
        return original(info)

    with patch.object(scanner, "signature", side_effect=crash):
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            reconcile.current(config, client["id"], vid, "manual.md")
    with store.connection(config) as db:
        assert db.execute("SELECT count(*) FROM working_versions").fetchone()[0] == 0
    reconcile.current(config, client["id"], vid, "manual.md")
    with store.connection(config) as db:
        assert db.execute("SELECT state FROM working_versions").fetchone()[0] == "complete"


def test_read_intent_rejects_new_observation_revision_and_keeps_original(config, identity_scope):
    client, vid, root = identity_scope
    observe(config, identity_scope)
    reconcile.current(config, client["id"], vid, "manual.md")
    with store.connection(config, write=True) as db:
        prior = dict(db.execute("SELECT * FROM working_versions").fetchone())
        db.execute("UPDATE files SET revision=revision+1 WHERE path='manual.md'")
    with pytest.raises(IntegrityError, match="vault_sync_source_changed"):
        working_intent.complete_read(
            config,
            vid,
            "manual.md",
            prior["revision"],
            json.loads(prior["value"]),
            root / "manual.md",
        )
    with store.connection(config) as db:
        assert dict(db.execute("SELECT * FROM working_versions").fetchone()) == prior


def test_real_identity_write_keeps_durable_prepared_boundary(config, identity_scope):
    client, vid, root = identity_scope
    track(config, identity_scope)
    observe(config, identity_scope)
    (root / "manual.md").write_text("# Actual external edit\n")
    changed(config, vid)

    def interrupted(*args, **kwargs):
        with store.connection(config) as db:
            assert db.execute("SELECT state FROM working_versions").fetchone()[0] == "prepared"
        raise RuntimeError("synthetic identity CAS interruption")

    with patch.object(note_identity_store, "save", side_effect=interrupted):
        with pytest.raises(RuntimeError, match="identity CAS"):
            reconcile.current(config, client["id"], vid, "manual.md")
    with store.connection(config) as db:
        assert db.execute("SELECT state FROM working_versions").fetchone()[0] == "prepared"
    reconcile.current(config, client["id"], vid, "manual.md")
    with store.connection(config) as db:
        assert db.execute("SELECT state FROM working_versions").fetchone()[0] == "complete"
