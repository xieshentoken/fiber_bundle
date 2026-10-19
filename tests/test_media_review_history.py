"""Read-only saved review display, exact bindings and all publication freeze states."""

import json

import pytest
from test_media_jobs import media_scope as media_scope
from test_media_processing import complete
from test_media_publication import approved, publish

from fiber_bundle import auth, media_jobs, media_review
from fiber_bundle.config import InvalidInput


def test_unreviewed_packet_is_read_only(http, config, media_scope):
    job = complete(config, media_scope)
    with media_jobs.connection(config) as db:
        auth.set_grant(
            db, media_scope[0]["id"], media_scope[1], add=True, query=True, original=True
        )
    for _ in range(2):
        packet = http.get(f"/admin/media/{job}/review-packet").json()
        assert packet["review"] is None and packet["publication"] is None
        assert not packet["review_gate"]["human_review_recorded"]
    with media_jobs.connection(config) as db:
        assert db.execute("SELECT count(*) FROM media_reviews").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM media_publications").fetchone()[0] == 0
    for name in ("media_review_history.js", "media_review_actions.js"):
        assert http.get("/static/" + name).status_code == 200


def test_packet_returns_latest_exact_judgments_and_time(http, config, media_scope):
    job = complete(config, media_scope)
    value, a = approved(config, media_scope, job)
    a["judgments"][0].update(verdict="reject", reason="<img onerror=alert(1)> synthetic reason")
    with media_jobs.connection(config) as db:
        saved = media_review.record(db, job, value, a)
        before = [tuple(r) for r in db.execute("SELECT * FROM media_reviews")]
    for _ in range(2):
        packet = http.get(f"/admin/media/{job}/review-packet").json()
        assert packet["review"]["review_id"] == saved["review_id"]
        assert packet["review"]["assessment"] == a
        assert packet["review"]["recorded_at"] > 0
        assert packet["review_gate"]["human_review_recorded"]
        assert not packet["review_gate"]["publication_ready"]
        assert not packet["review_gate"]["knowledge_verified"]
    with media_jobs.connection(config) as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM media_reviews")] == before


@pytest.mark.parametrize("state", ["prepared", "publishing", "conflict", "published"])
def test_publication_state_is_visible_and_review_remains_frozen(http, config, media_scope, state):
    job = complete(config, media_scope)
    _, a = approved(config, media_scope, job)
    published = publish(config, job)
    with media_jobs.connection(config) as db:
        db.execute("UPDATE media_publications SET state=? WHERE job_id=?", (state, job))
    packet = http.get(f"/admin/media/{job}/review-packet").json()
    assert packet["publication"] == {"state": state, "relative_path": published["relative_path"]}
    response = http.post(f"/admin/media/{job}/review", json=a)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_input"
    with media_jobs.connection(config) as db:
        assert db.execute("SELECT count(*) FROM media_reviews").fetchone()[0] == 1


@pytest.mark.parametrize(
    "field", ["source_sha256", "result_sha256", "glossary_revision", "glossary_sha256"]
)
def test_changed_saved_evidence_is_never_displayed_as_current(http, config, media_scope, field):
    job = complete(config, media_scope)
    _, a = approved(config, media_scope, job)
    a[field] = a[field] + 1 if field == "glossary_revision" else "f" * 64
    with media_jobs.connection(config) as db:
        db.execute(
            "UPDATE media_reviews SET assessment_json=? WHERE job_id=?", (json.dumps(a), job)
        )
    response = http.get(f"/admin/media/{job}/review-packet")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_input"
    with media_jobs.connection(config) as db:
        from fiber_bundle import media_processing

        value = media_processing.result_locked(config, db, media_scope[0]["id"], job)
        with pytest.raises(InvalidInput, match="media_review_bound_evidence_changed"):
            media_review.saved(db, job, value)


@pytest.mark.parametrize("permission", ["query", "original"])
def test_revocation_hides_saved_review(http, config, media_scope, permission):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    with media_jobs.connection(config) as db:
        grants = {"add": True, "query": True, "original": True, permission: False}
        auth.set_grant(db, media_scope[0]["id"], media_scope[1], **grants)
    assert http.get(f"/admin/media/{job}/review-packet").status_code == 404
