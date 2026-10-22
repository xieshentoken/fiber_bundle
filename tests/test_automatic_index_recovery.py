"""Recovery proves real publication, current files and a ready index; it never replays work."""

import json
import sqlite3
from unittest.mock import patch

import pytest
from test_automatic_ingestion import admitted, explicit_ticks  # noqa: F401
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner

from fiber_bundle import (
    auth,
    automatic_ingestion,
    automatic_jobs,
    automatic_policy,
    automatic_recovery,
    database,
    note_search_build,
    note_search_cache,
)
from fiber_bundle.config import IntegrityError, InvalidInput


@pytest.fixture
def saved(config, store, owner):
    cid, vid, _, job, _ = admitted(config, owner)
    assert automatic_ingestion.tick(config)  # review
    assert automatic_ingestion.tick(config)  # real files + committed publication
    with patch("fiber_bundle.note_search_build.build", side_effect=OSError("synthetic fault")):
        assert automatic_ingestion.tick(config)
    # A later successful index update does not settle the historical ingestion job.
    note_search_build.build(config, cid, vid)
    with database.connect(config.state_root) as db:
        current = automatic_jobs.get(db, job["id"])
        assert current["state"] == "needs_review" and current["phase"] == "index"
        publication = dict(db.execute("SELECT * FROM document_publications").fetchone())
        location = dict(db.execute("SELECT * FROM publication_locations").fetchone())
    root = config.project_root / "test vault/temp_vault1"
    files = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    return cid, vid, job["id"], files, publication, location


def test_ready_index_settles_history_after_policy_changed_without_replay(config, saved):
    cid, vid, identity, files, publication, location = saved
    with database.connect(config.state_root) as db:
        job = automatic_jobs.get(db, identity)
        original_binding = job["value"]["binding"]
        automatic_policy.save(config, db, vid, 1, automatic_policy.Policy())
        with pytest.raises(InvalidInput, match="automatic_authorization_changed"):
            automatic_jobs.authorize(config, db, job)
        with (
            patch("fiber_bundle.note_search_build.build", side_effect=AssertionError("rebuild")),
            patch(
                "fiber_bundle.intake_summary.after_source", side_effect=AssertionError("summary")
            ),
            patch("fiber_bundle.organization.jobs.notify", side_effect=AssertionError("organize")),
        ):
            result = automatic_recovery.resume(config, db, vid, identity)
        assert result == {
            "state": "complete",
            "phase": "index",
            "existing_index_verified": True,
            "new_model_attempt": False,
            "post_processing_started": False,
        }
        final = automatic_jobs.get(db, identity)
        assert final["value"]["binding"] == original_binding
        assert final["value"]["failure_history"][-1]["reason"] == (
            "automatic_index_requires_reconciliation"
        )
        assert final["value"]["index_reconciliation"]["notes"]
        assert final["reason"] is None
        assert dict(db.execute("SELECT * FROM document_publications").fetchone()) == publication
        assert dict(db.execute("SELECT * FROM publication_locations").fetchone()) == location
        assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
        assert not next(j for j in automatic_jobs.listing(db, vid) if j["id"] == identity)[
            "action_required"
        ]
        with pytest.raises(InvalidInput, match="unavailable"):
            automatic_recovery.resume(config, db, vid, identity)
    assert all(p.read_bytes() == raw for p, raw in files.items())


@pytest.mark.parametrize("fault", ["absent", "stale", "missing_entry", "uncommitted"])
def test_unproven_index_only_queues_original_authorized_index_phase(config, saved, fault):
    _, vid, identity, _, _, _ = saved
    path = note_search_cache.location(config, vid)
    if fault == "absent":
        path.unlink()
    elif fault in {"stale", "missing_entry"}:
        with sqlite3.connect(path) as index:
            index.execute("UPDATE generation SET fingerprint='outdated'") if fault == "stale" else (
                index.execute("DELETE FROM notes")
            )
    with database.connect(config.state_root) as db:
        if fault == "uncommitted":
            db.execute("UPDATE publication_locations SET state='reconcile'")
        result = automatic_recovery.resume(config, db, vid, identity)
        assert result["state"] == "queued" and result["phase"] == "index"
        assert not result["new_model_attempt"]
        assert "index_reconciliation" not in automatic_jobs.get(db, identity)["value"]


@pytest.mark.parametrize("permission", ["query", "original", "add"])
def test_revoked_current_access_prevents_acknowledgement(config, saved, permission):
    cid, vid, identity, _, _, _ = saved
    permissions = {"query": True, "original": True, "add": True, permission: False}
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, **permissions)
        with pytest.raises(auth.Denied):
            automatic_recovery.resume(config, db, vid, identity)
        assert automatic_jobs.get(db, identity)["state"] == "needs_review"


@pytest.mark.parametrize("field", ["sha256", "file_key", "identity", "relative_path"])
def test_cache_provenance_or_body_mismatch_never_completes(config, saved, field):
    _, vid, identity, _, _, _ = saved
    with sqlite3.connect(note_search_cache.location(config, vid)) as index:
        path, raw = index.execute(
            "SELECT path,metadata FROM notes WHERE path LIKE 'Inbox/%'"
        ).fetchone()
        value = json.loads(raw)
        value[field] = [0, 0, "0x0.0p+0"] if field == "file_key" else "wrong"
        index.execute("UPDATE notes SET metadata=? WHERE path=?", (json.dumps(value), path))
    with database.connect(config.state_root) as db:
        with pytest.raises(IntegrityError, match="automatic_index_source_changed"):
            automatic_recovery.resume(config, db, vid, identity)
        assert automatic_jobs.get(db, identity)["state"] == "needs_review"


@pytest.mark.parametrize("state", ["cancelled", "complete", "queued"])
def test_terminal_or_active_jobs_are_not_recovered(config, saved, state):
    _, vid, identity, _, _, _ = saved
    with database.connect(config.state_root) as db:
        db.execute("UPDATE automatic_jobs SET state=? WHERE id=?", (state, identity))
        with pytest.raises(InvalidInput, match="unavailable"):
            automatic_recovery.resume(config, db, vid, identity)
