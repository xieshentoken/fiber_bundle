"""Ordinary retries freeze new attempts and do not overwrite failed runs."""

from document_support import pdf_bytes
from test_automatic_ingestion import admitted, setup
from test_source_imports import request, upload
from test_source_processing import WORKERS

from fiber_bundle import (
    automatic_document_continuation,
    automatic_jobs,
    automatic_recovery,
    automatic_review,
    database,
    source_import_existing,
    source_processing_policy,
)

pytest_plugins = ["test_source_imports", "test_execution"]


def test_explicit_failed_ordinary_retry_is_new_and_idempotent(config, store, owner):
    _, vid, _, job, rid = admitted(config, owner)
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE execution_runs SET state='failed',error_code='document_worker_failed' "
            "WHERE id=?",
            (rid,),
        )
        current = automatic_jobs.get(db, job["id"])
        automatic_jobs.settle(db, current, "needs_review", reason="automatic_extraction_failed")
        before = dict(db.execute("SELECT * FROM execution_runs WHERE id=?", (rid,)).fetchone())
        one = automatic_recovery.resume(config, db, vid, job["id"])
        two = automatic_recovery.resume(config, db, vid, job["id"])
        assert one == two
        assert not one["new_model_attempt"] and one["original_attempt_preserved"]
        successor = automatic_jobs.get(db, one["successor_job_id"])
        assert successor["value"]["supersedes_job"] == job["id"]
        source = db.execute(
            "SELECT * FROM source_imports WHERE id=?", (successor["source_id"],)
        ).fetchone()
        assert source["run_id"] is None
        assert (
            dict(db.execute("SELECT * FROM execution_runs WHERE id=?", (rid,)).fetchone()) == before
        )
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


def test_scanned_channel_pdf_selects_ocr_continuation_only(config, store, owner):
    cid, vid = setup(config, owner)
    settings = source_processing_policy.save(config, None, WORKERS, None)
    raw = pdf_bytes()
    with database.connect(config.state_root) as db:
        original = upload(
            config,
            db,
            request(cid, vid, raw, "scan.pdf", sha=settings["configuration_sha256"]),
            raw,
        )
        imported = source_import_existing.create(
            config,
            db,
            cid,
            vid,
            original["receipt_id"],
            original["asset_id"],
            "ordinary",
            {"kind": "channel_original"},
        )
        identity = automatic_jobs.enqueue(config, db, cid, vid, "document", imported["id"])
        current = automatic_jobs.get(db, identity)
        before = dict(
            db.execute("SELECT * FROM source_imports WHERE id=?", (imported["id"],)).fetchone()
        )
        assert automatic_document_continuation.needs_ocr(
            config, db, current, {"pages_needing_ocr": [1]}
        )
        pending = automatic_jobs.get(db, identity)
        assert pending["reason"] == "ocr_profile_required"
        assert pending["value"]["retry_profile"] == "ocr"
        assert not db.execute("SELECT 1 FROM automatic_jobs WHERE id!=?", (identity,)).fetchone()
        assert (
            dict(
                db.execute("SELECT * FROM source_imports WHERE id=?", (imported["id"],)).fetchone()
            )
            == before
        )


def test_successful_ocr_is_not_forced_into_human_review():
    candidate = {
        "schema": 2,
        "source_sha256": "a" * 64,
        "result_sha256": "b" * 64,
        "complete": True,
        "segments": [
            {
                "id": "U1",
                "text": "OCR text",
                "locator": {},
                "ocr_required": False,
                "review_reasons": ["ocr_review"],
            }
        ],
    }
    assessment = automatic_review.document(candidate, {}, None)
    assert assessment["decision"] == "accept"
    assert "human_visual_review" in assessment["unverified_dimensions"]
    candidate["segments"][0]["ocr_required"] = True
    assert automatic_review.document(candidate, {}, None)["decision"] == "uncertain"
