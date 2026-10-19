"""Automatic publication uses a genuine credential, durable admission and existing publisher."""

import json
from unittest.mock import patch

import pytest
from test_publication_layout import activate
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner
from test_source_imports import request, upload
from test_source_processing import WORKERS, worker

from fiber_bundle import (
    auth,
    automatic_ingestion,
    automatic_jobs,
    automatic_policy,
    database,
    document_evidence,
    document_publication,
    document_review,
    search,
    source_import_execution,
    source_processing_policy,
    vault_save_contract,
)
from fiber_bundle.config import InvalidInput


@pytest.fixture(autouse=True)
def explicit_ticks(monkeypatch):
    # This module controls each durable phase; scheduler lifecycle is tested separately.
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)


def setup(config, owner):
    cid, _, ids = owner
    vid = ids["personal"]
    settings = vault_save_contract.settings_wire(vault_save_contract.unified_settings())
    settings.pop("layout_mode")
    activate(config, vid, **settings)
    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            vid,
            0,
            automatic_policy.Policy(
                client_id=cid,
                review_mode="automatic",
                publish_mode="automatic",
                allowed_kinds=["document"],
            ),
        )
    return cid, vid


def admitted(config, owner):
    cid, vid = setup(config, owner)
    settings = source_processing_policy.save(config, None, WORKERS, None)
    raw = b"# Synthetic UV\n\nverified-auto-token 2.4 MPa\n"
    with database.connect(config.state_root) as db:
        item = upload(
            config,
            db,
            request(cid, vid, raw, "synthetic.md", "process", settings["configuration_sha256"]),
            raw,
        )
        job = automatic_jobs.get(db, item["automatic"]["id"])

    def launch(config, db, client, identity):
        rid = source_import_execution.prepare(config, db, client, identity)
        db.execute("UPDATE source_task_bindings SET launch_state='launched' WHERE run_id=?", (rid,))
        return {}

    # Process launch is isolated; extraction/result validation/rendering/commit/index are real.
    with patch("fiber_bundle.source_import_execution.launch", side_effect=launch):
        assert automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        rid = automatic_jobs.get(db, job["id"])["value"]["run_id"]
    with patch("fiber_bundle.document_worker.execute_document", side_effect=worker):
        source_import_execution.execute(config, rid)
    return cid, vid, item, job, rid


def test_new_markdown_reaches_search_without_human_assessment(config, store, owner):
    cid, vid, item, job, rid = admitted(config, owner)
    for _ in range(3):
        advanced = automatic_ingestion.tick(config)
        with database.connect(config.state_root) as db:
            assert advanced, automatic_jobs.get(db, job["id"])
    with database.connect(config.state_root) as db:
        final = automatic_jobs.get(db, job["id"])
        assert final["state"] == "complete"
        assert db.execute("SELECT COUNT(*) FROM document_reviews").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
        credential = json.loads(db.execute("SELECT value FROM automatic_reviews").fetchone()[0])
        assert credential["actor"] == "system_rules" and credential["task_id"] == rid
        assert not {"source_reviewed", "visual_reviewed"} & credential.keys()
        hit = search.vault_search(config, db, cid, "verified-auto-token", [vid])["results"][0]
        assert hit["note_path"].startswith("Inbox/")
        assert hit["review_type"] == "system" and hit["review_actor"] == "system_rules"
        assert "人工审核" not in hit["source_label"]
        before = db.execute("SELECT manifest_json FROM document_publications").fetchone()[0]
    assert not automatic_ingestion.tick(config)
    automatic_ingestion.recover(config)
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT COUNT(*) FROM document_publications").fetchone()[0] == 1
        assert db.execute("SELECT manifest_json FROM document_publications").fetchone()[0] == before


@pytest.mark.parametrize("fault", ["disable", "regrant", "cancel", "human_reject"])
def test_changed_authorization_or_human_rejection_never_publishes(config, store, owner, fault):
    cid, vid, _, job, rid = admitted(config, owner)
    with database.connect(config.state_root) as db:
        if fault == "disable":
            automatic_policy.save(config, db, vid, 1, automatic_policy.Policy())
        elif fault == "regrant":
            auth.set_grant(db, cid, vid, query=True, original=False, add=True)
            auth.set_grant(db, cid, vid, query=True, original=True, add=True)
        elif fault == "cancel":
            automatic_jobs.settle(db, job, "cancelled")
        else:
            _, candidate = document_evidence.load(config, db, cid, rid)
            document_review.record(
                db,
                rid,
                candidate,
                {
                    "source_sha256": candidate["source_sha256"],
                    "result_sha256": candidate["result_sha256"],
                    "source_reviewed": True,
                    "visual_reviewed": True,
                    "judgments": [
                        {
                            "unit_id": u["unit_id"],
                            "verdict": "reject",
                            "reason": "actual test rejection",
                        }
                        for u in document_review.units(candidate)
                    ],
                },
            )
    for _ in range(3):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        assert not db.execute("SELECT 1 FROM document_publications").fetchone()
        assert automatic_jobs.get(db, job["id"])["state"] in {
            "needs_review",
            "cancelled",
            "reconcile",
        }


def test_manual_gate_and_system_credential_cannot_be_forged(config, store, owner):
    _, _, _, job, rid = admitted(config, owner)
    with database.connect(config.state_root) as db:
        with pytest.raises(InvalidInput, match="not reviewed"):
            document_publication.publish_locked(config, db, rid)
        with pytest.raises(InvalidInput, match="unavailable"):
            document_publication.publish_locked(config, db, rid, system_review_id=job["id"])
