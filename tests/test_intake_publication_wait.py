"""Pending archive dependencies never replay extraction, models, or side effects."""

import json
import time
from uuid import uuid4

import pytest
from test_article_fetch import Reply, install
from test_articles import PNG, X_URL, current_html
from test_channel_intake import receive, records
from test_intake_layout_recovery import configure

from fiber_bundle import automatic_ingestion, automatic_jobs, automatic_recovery, database
from fiber_bundle.channels import intake_extraction, intake_outcomes, intake_processing
from fiber_bundle.config import IntegrityError

pytest_plugins = ["test_channel_intake", "test_execution"]


def captured(config, entry, policy, monkeypatch):
    configure(config, entry, policy, monkeypatch)
    batch = receive(config, entry, text=X_URL)
    intake_processing.advance(config, entry[0]["id"])
    install(
        monkeypatch,
        [
            Reply(200, current_html(), [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    intake_extraction.advance(config, entry[0]["id"])
    automatic_ingestion.tick(config)
    return batch, json.loads(records(config, entry)[0]["value"])["links"][0]


def test_archive_wait_is_precise_and_resumes_cached_publication(config, entry, policy, monkeypatch):
    batch, capture = captured(config, entry, policy, monkeypatch)
    oid = str(uuid4())
    with database.connect(config.state_root) as db:
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,?,?,?,?)",
            (oid, entry[1].vault_id, "note_placement", "reconcile", "{}", time.time(), time.time()),
        )
    automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        job = automatic_jobs.get(db, db.execute("SELECT id FROM automatic_jobs").fetchone()[0])
        assert job["state"] == "waiting" and job["phase"] == "publish"
        assert job["reason"] == "placement_reconciliation_required"
        view = automatic_jobs.listing(db, entry[1].vault_id)[0]
        assert view["display_state"] == "waiting_publication"
        assert view["publication_blockers"][0]["id"] == oid
        assert not db.execute("SELECT 1 FROM publication_locations").fetchone()
        value = json.loads(records(config, entry)[0]["value"])
        receipt = intake_outcomes.outcome(db, entry[0], batch, value)
        assert receipt["stage"] == "waiting_dependency" and "正文和附件已提取" in receipt["body"]
        assert "可能尚未取得" not in receipt["body"]
        # Existing releases left this same pre-publication interruption in reconcile.
        db.execute("UPDATE automatic_jobs SET state='reconcile' WHERE id=?", (job["id"],))
        with pytest.raises(IntegrityError, match="placement_reconciliation_required"):
            automatic_recovery.resume(
                config, db, entry[1].vault_id, job["id"], publication_only=True
            )
        assert not automatic_jobs.get(db, job["id"])["value"].get("failure_history")
        db.execute("UPDATE vault_operations SET state='completed' WHERE id=?", (oid,))
        automatic_recovery.resume(config, db, entry[1].vault_id, job["id"], publication_only=True)
    monkeypatch.setattr(
        "fiber_bundle.organization.jobs.notify", lambda *a, **k: pytest.fail("no new organization")
    )
    monkeypatch.setattr(
        "fiber_bundle.intake_summary.after_source", lambda *a, **k: pytest.fail("no model followup")
    )
    for _ in range(4):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        final = automatic_jobs.get(db, job["id"])
        assert final["state"] == "complete"
        assert final["value"]["recovery_scope"] == "publication_and_index"
        assert db.execute("SELECT count(*) FROM publication_locations").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    after = json.loads(records(config, entry)[0]["value"])["links"][0]
    for field in ("attempt_id", "receipt", "files", "metadata"):
        assert after[field] == capture[field]


def test_waiting_publish_continues_after_owner_finishes(config, entry, policy, monkeypatch):
    captured(config, entry, policy, monkeypatch)
    oid = str(uuid4())
    with database.connect(config.state_root) as db:
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,?,?,?,?)",
            (oid, entry[1].vault_id, "note_placement", "reconcile", "{}", time.time(), time.time()),
        )
    automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        row = db.execute("SELECT id,value FROM automatic_jobs").fetchone()
        value = json.loads(row["value"])
        value["availability_wait"]["next_check"] = 0
        db.execute("UPDATE vault_operations SET state='completed' WHERE id=?", (oid,))
        db.execute("UPDATE automatic_jobs SET value=? WHERE id=?", (json.dumps(value), row["id"]))
    automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        job = automatic_jobs.get(db, row["id"])
        assert job["phase"] == "index" and job["state"] == "queued"
        assert not job["value"].get("publication_blockers")
        assert db.execute("SELECT count(*) FROM publication_locations").fetchone()[0] == 1
