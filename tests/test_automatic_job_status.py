"""Pending projection distinguishes historical attempts, saving and real review work."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_source_imports import owner as owner

from fiber_bundle import automatic_ingestion, automatic_jobs, database
from fiber_bundle.config import IntegrityError
from fiber_bundle.inputs import canonical

pytest_plugins = ["test_source_imports", "test_execution"]


@pytest.fixture(autouse=True)
def explicit_tasks(monkeypatch):
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)


def insert(db, cid, vid, *, state="needs_review", phase="review", kind="intake", at=1, value=None):
    identity = str(uuid4())
    db.execute(
        "INSERT INTO automatic_jobs VALUES(?,?,?,?,?,1,?,?,?,NULL,?,?)",
        (identity, vid, cid, kind, str(uuid4()), phase, state, canonical(value or {}), at, at),
    )
    return identity


def test_superseded_attempt_is_history_without_updating_any_row(config, owner, http):
    cid, _, ids = owner
    vid = ids["personal"]
    with database.connect(config.state_root) as db:
        old = insert(db, cid, vid)
        successor = insert(db, cid, vid, state="complete", at=2, value={"supersedes_job": old})
        reviewed = insert(db, cid, vid, state="reviewed", phase="publish")
        before = list(map(tuple, db.execute("SELECT * FROM automatic_jobs ORDER BY id")))
        jobs = {j["id"]: j for j in automatic_jobs.listing(db, vid)}
        assert jobs[old]["state"] == "needs_review"
        assert jobs[old]["display_state"] == "superseded"
        assert jobs[old]["superseded_by"] == successor
        assert not jobs[old]["action_required"]
        assert jobs[reviewed]["display_state"] == "awaiting_review"
        assert jobs[reviewed]["action_required"]
        assert jobs[old]["vault_id"] == vid
        assert list(map(tuple, db.execute("SELECT * FROM automatic_jobs ORDER BY id"))) == before
    response = http.get(f"/admin/vault-catalog/{vid}/automatic-jobs")
    assert response.status_code == 200
    assert next(j for j in response.json() if j["id"] == old)["superseded_by"] == successor


@pytest.mark.parametrize("mismatch", ["vault", "client", "kind", "older"])
def test_unrelated_or_older_task_cannot_hide_a_failure(config, owner, mismatch):
    from fiber_bundle import auth

    cid, _, ids = owner
    with database.connect(config.state_root) as db:
        old = insert(db, cid, ids["personal"], at=2)
        other = auth.create_client(db, "other", False)["id"]
        insert(
            db,
            other if mismatch == "client" else cid,
            ids["supplier"] if mismatch == "vault" else ids["personal"],
            kind="document" if mismatch == "kind" else "intake",
            at=1 if mismatch == "older" else 3,
            value={"supersedes_job": old},
        )
        job = next(j for j in automatic_jobs.listing(db, ids["personal"]) if j["id"] == old)
        assert job["display_state"] == "failed" and job["action_required"]


def test_successor_outside_recent_page_still_resolves_old_attempt(config, owner):
    cid, _, ids = owner
    with database.connect(config.state_root) as db:
        old = insert(db, cid, ids["personal"])
        new = insert(
            db, cid, ids["personal"], state="complete", at=2, value={"supersedes_job": old}
        )
        for i in range(101):
            insert(db, cid, ids["personal"], state="complete", at=10 + i)
        db.execute("UPDATE automatic_jobs SET updated_at=1000 WHERE id=?", (old,))
        jobs = automatic_jobs.listing(db, ids["personal"])
        assert len(jobs) == 100 and new not in {j["id"] for j in jobs}
        assert next(j for j in jobs if j["id"] == old)["superseded_by"] == new


def test_saved_document_with_index_failure_is_not_an_unpublished_review(config, owner, store):
    from test_automatic_ingestion import admitted

    cid, vid, imported, job, run = admitted(config, owner)
    assert automatic_ingestion.tick(config)  # real review
    assert automatic_ingestion.tick(config)  # real publication
    with patch(
        "fiber_bundle.note_search_build.build", side_effect=IntegrityError("layout_rebind_required")
    ):
        assert automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        row = next(j for j in automatic_jobs.listing(db, vid) if j["id"] == job["id"])
        assert row["display_state"] == "index_reconcile"
        assert row["publication_state"] == "published"
        assert row["entry_note_path"].endswith(".md")
        assert row["title"] and row["action_required"]
        assert row["state"] == "needs_review" and row["phase"] == "index"
        assert "索引" in row["display_message"]
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
        location = db.execute(
            "SELECT value FROM publication_locations WHERE publication_id=?", (run,)
        ).fetchone()
        plan = json.loads(location[0])
        files = {
            p: (config.project_root / "test vault/temp_vault1" / p).read_bytes()
            for p in plan["outputs"]
        }
    assert not automatic_ingestion.tick(config)
    assert all(
        (config.project_root / "test vault/temp_vault1" / p).read_bytes() == raw
        for p, raw in files.items()
    )


def test_known_intake_failures_keep_actionable_chinese_reason(config, owner):
    cid, _, ids = owner
    with database.connect(config.state_root) as db:
        task = insert(db, cid, ids["personal"])
        db.execute("UPDATE automatic_jobs SET reason=? WHERE id=?", ("model_timeout", task))
        row = next(j for j in automatic_jobs.listing(db, ids["personal"]) if j["id"] == task)
        assert row["display_state"] == "failed"
        assert "模型处理超时" in row["display_message"]
        assert row["reason"] == "model_timeout"
