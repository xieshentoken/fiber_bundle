"""A displayed review ID cannot publish a newer judgment, even if both accept."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_media_jobs import media_scope as media_scope
from test_media_processing import complete
from test_media_publication import approved

from fiber_bundle import auth, media_jobs, media_publication, media_review, publication_files
from fiber_bundle.config import InvalidInput


def ledger(config, job):
    with media_jobs.connection(config) as db:
        return db.execute("SELECT * FROM media_publications WHERE job_id=?", (job,)).fetchall()


@pytest.mark.parametrize("verdict", ["accept", "reject", "uncertain"])
def test_old_window_cannot_publish_replaced_review(http, config, media_scope, verdict):
    job = complete(config, media_scope)
    value, a = approved(config, media_scope, job)
    url = f"/admin/media/{job}"
    displayed = http.get(url + "/review-packet").json()["review"]
    a["judgments"][0].update(verdict=verdict, reason="New synthetic review from another window.")
    with media_jobs.connection(config) as db:
        current = media_review.record(db, job, value, a)
        with pytest.raises(InvalidInput, match="media_publication_review_changed"):
            media_publication.publish_locked(config, db, job, review_id=displayed["review_id"])
    with patch.object(publication_files, "stage", wraps=publication_files.stage) as stage:
        response = http.post(url + "/publish", json={"review_id": displayed["review_id"]})
        assert response.status_code == 422 and response.json() == {"error": "invalid_input"}
        stage.assert_not_called()
    assert not ledger(config, job)
    vault = config.project_root / dict(config.vaults)["personal"]
    assert not (vault / ("Media-" + job)).exists()
    assert not (vault / (".fiber-media-" + job)).exists()
    fresh = http.get(url + "/review-packet").json()
    assert fresh["review"]["review_id"] == current["review_id"]
    assert fresh["review"]["assessment"] == a
    if verdict == "accept":
        assert (
            http.post(url + "/publish", json={"review_id": current["review_id"]}).json()["state"]
            == "published"
        )
    else:
        assert (
            http.post(url + "/publish", json={"review_id": current["review_id"]}).status_code == 422
        )
        assert not ledger(config, job)


def test_matching_review_is_written_to_provenance_and_historical_retry(http, config, media_scope):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    url = f"/admin/media/{job}"
    identity = http.get(url + "/review-packet").json()["review"]["review_id"]
    response = http.post(url + "/publish", json={"review_id": identity})
    assert response.status_code == 200
    root = config.project_root / dict(config.vaults)["personal"] / response.json()["relative_path"]
    assert json.loads((root / "provenance.json").read_text())["review_id"] == identity
    (root / "note.md").write_text("Preserve a synthetic user's edit.")
    assert http.post(url + "/publish", json={"review_id": identity}).json()["historical"]
    assert (root / "note.md").read_text() == "Preserve a synthetic user's edit."
    assert http.post(url + "/publish", json={"review_id": str(uuid4())}).status_code == 422
    assert len(ledger(config, job)) == 1


def test_bound_review_survives_rename_response_interruption(http, config, media_scope):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    identity = http.get(f"/admin/media/{job}/review-packet").json()["review"]["review_id"]
    rename = publication_files.rename_exclusive

    def interrupted(*args):
        rename(*args)
        raise RuntimeError("synthetic interrupted response")

    with media_jobs.connection(config) as db:
        with patch.object(publication_files, "rename_exclusive", side_effect=interrupted):
            with pytest.raises(RuntimeError):
                media_publication.publish_locked(config, db, job, review_id=identity)
    assert (
        http.get(f"/admin/media/{job}/review-packet").json()["publication"]["state"] == "publishing"
    )
    with media_jobs.connection(config) as db:
        assert (
            media_publication.publish_locked(config, db, job, review_id=identity)["state"]
            == "published"
        )


@pytest.mark.parametrize(
    "body",
    [{}, {"review_id": "bad"}, {"review_id": 42}, {"review_id": str(uuid4()), "extra": True}],
)
def test_invalid_bound_publish_body_has_no_effect(http, config, media_scope, body):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    assert http.post(f"/admin/media/{job}/publish", json=body).status_code == 422
    assert not ledger(config, job)


def test_legacy_bodyless_explicit_publication_is_compatible(http, config, media_scope):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    assert http.post(f"/admin/media/{job}/publish").json()["state"] == "published"


@pytest.mark.parametrize("permission", ["add", "query", "original"])
def test_bound_publish_still_rechecks_current_permissions(http, config, media_scope, permission):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    identity = http.get(f"/admin/media/{job}/review-packet").json()["review"]["review_id"]
    with media_jobs.connection(config) as db:
        grants = {"add": True, "query": True, "original": True, permission: False}
        auth.set_grant(db, media_scope[0]["id"], media_scope[1], **grants)
    assert http.post(f"/admin/media/{job}/publish", json={"review_id": identity}).status_code == 404
    assert not ledger(config, job)


def test_bound_publish_still_requires_csrf(http, config, media_scope):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    identity = http.get(f"/admin/media/{job}/review-packet").json()["review"]["review_id"]
    http.headers.pop("x-csrf-token")
    assert http.post(f"/admin/media/{job}/publish", json={"review_id": identity}).status_code == 403
    assert not ledger(config, job)
