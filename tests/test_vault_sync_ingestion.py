"""No model/network: extracted fixtures wait safely, publish once and index incrementally."""

import time
from unittest.mock import patch

import pytest
from test_automatic_ingestion import admitted
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner
from test_vault_sync_reconcile import observe

from fiber_bundle import auth, automatic_ingestion, automatic_jobs, database, vaults
from fiber_bundle.config import IntegrityError
from fiber_bundle.vault_sync import index, scanner
from fiber_bundle.vault_sync import store as sync


@pytest.fixture(autouse=True)
def explicit_ticks(monkeypatch):
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)


def test_preflight_wait_survives_restart_without_replaying_extraction(config, store, owner):
    cid, vid, item, job, run = admitted(config, owner)
    with patch(
        "fiber_bundle.automatic_jobs.authorize",
        side_effect=IntegrityError("cloud_item_not_downloaded"),
    ):
        assert automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        held = automatic_jobs.get(db, job["id"])
        assert held["state"] == "waiting" and held["phase"] == "review"
        assert held["value"]["run_id"] == run
        assert held["value"]["availability_wait"]["next_check"] > time.time()
        assert automatic_jobs.claim(db) is None
        held["value"]["availability_wait"]["next_check"] = 0
        automatic_jobs.settle(db, held, "waiting")
    automatic_ingestion.recover(config)
    with patch(
        "fiber_bundle.source_import_execution.launch",
        side_effect=AssertionError("extraction replay"),
    ):
        assert automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        assert automatic_jobs.get(db, job["id"])["phase"] == "publish"


def test_new_import_uses_event_index_without_full_scan_or_double_publication(config, store, owner):
    cid, vid, item, job, run = admitted(config, owner)
    with database.connect(config.state_root) as db:
        root = vaults.checked_vault(config, auth.grant(db, cid, vid, "query"))
    observe(config, ({"id": cid}, vid, root))
    scanner.heartbeat(config, vid)
    index.update(config, cid, vid)
    assert automatic_ingestion.tick(config)
    assert automatic_ingestion.tick(config)
    with patch("fiber_bundle.note_search_build.build", side_effect=AssertionError("full scan")):
        assert automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        held = automatic_jobs.get(db, job["id"])
        assert held["state"] == "waiting" and held["phase"] == "index"
        assert held["reason"] == "automatic_saved_index_updating"
        held["value"]["availability_wait"]["next_check"] = 0
        automatic_jobs.settle(db, held, "waiting")
    while tasks := sync.due(config, now=time.time() + 100):
        for task in tasks:
            scanner.apply(config, task, now=time.time() + 100)
    scanner.heartbeat(config, vid)
    index.update(config, cid, vid)
    with patch("fiber_bundle.note_search_build.build", side_effect=AssertionError("full scan")):
        assert automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        final = automatic_jobs.get(db, job["id"])
        assert final["state"] == "complete"
        assert db.execute("SELECT count(*) FROM document_publications").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


def test_receiver_permissions_are_metadata_only_but_revocation_blocks(config, store, owner):
    from fiber_bundle.channels.validation import ChannelValidationError, require_permissions

    cid, _, ids = owner
    vid = ids["personal"]
    channel = {"value": {"client_id": cid, "vault_id": vid, "add": True, "original": True}}
    with (
        database.connect(config.state_root) as db,
        patch.object(vaults, "checked_vault", side_effect=AssertionError("receiver Vault read")),
    ):
        require_permissions(config, db, channel, transport=True)
        auth.set_grant(db, cid, vid, query=False, original=True, add=True)
        with pytest.raises(ChannelValidationError):
            require_permissions(config, db, channel, transport=True)
