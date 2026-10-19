"""Media administrator read contracts, ownership and unchanged publication gates."""

from contextlib import closing

import pytest
from test_media_jobs import create
from test_media_jobs import media_scope as media_scope
from test_media_processing import complete

from fiber_bundle import auth, database, media_jobs, objects


@pytest.fixture
def processed(config, media_scope):
    job = complete(config, media_scope)
    with media_jobs.connection(config) as db:
        auth.set_grant(
            db, media_scope[0]["id"], media_scope[1], add=True, query=True, original=True
        )
    return job


@pytest.mark.parametrize("suffix", ["review-packet", "source", "frames/missing"])
def test_missing_media_job_remains_404(http, suffix):
    response = http.get(f"/admin/media/missing/{suffix}")
    assert response.status_code == 404
    assert response.json() == {"error": "request_denied"}


def test_listing_orders_processed_tasks_and_excludes_unprocessed(http, config, media_scope):
    first = complete(config, media_scope)
    second = complete(config, media_scope)
    pending = create(config, media_scope)
    with media_jobs.connection(config) as db:
        db.execute("UPDATE media_processing SET created_at=1 WHERE job_id=?", (first,))
        db.execute("UPDATE media_processing SET created_at=2 WHERE job_id=?", (second,))
    response = http.get("/admin/media")
    assert response.status_code == 200
    assert response.json() == {
        "tasks": [
            {
                "job_id": job,
                "state": "complete",
                "client_id": media_scope[0]["id"],
                "vault_id": media_scope[1],
                "publication_state": None,
                "automatic_state": None,
            }
            for job in (second, first)
        ]
    }
    assert pending not in {t["job_id"] for t in response.json()["tasks"]}


def test_source_bytes_metadata_and_readonly_unreviewed_packet(http, config, media_scope, processed):
    response = http.get(f"/admin/media/{processed}/source")
    assert response.status_code == 200
    assert response.content == media_scope[2][0][2]
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["content-disposition"] == 'attachment; filename="source.wav"'
    value = http.get(f"/admin/media/{processed}/review-packet").json()
    assert value["candidate"]["job_id"] == processed
    assert value["mode"] == "audio_only"
    assert value["source_context"]["platform"] is None
    assert value["review"] is None and value["publication"] is None
    assert value["review_gate"]["publication_ready"] is False
    assert http.get(f"/admin/media/{processed}/frames/missing").status_code == 404
    # A missing assessment must not be mistaken for permission to publish or delete.
    for action in ("publish", "cleanup-plan"):
        assert http.post(f"/admin/media/{processed}/{action}").status_code == 422
    with closing(database.connect(config.state_root)) as db:
        for table in ("media_reviews", "media_publications", "media_cleanup", "model_calls"):
            assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0


@pytest.mark.parametrize("suffix", ["", "/review-packet", "/source", "/frames/missing"])
def test_bearer_cannot_read_administrator_media(http, media_scope, processed, suffix):
    http.cookies.clear()
    path = "/admin/media" + ("/" + processed + suffix if suffix else "")
    response = http.get(path, headers={"Authorization": "Bearer " + media_scope[0]["token"]})
    assert response.status_code == 401


@pytest.mark.parametrize("change", ["query", "original", "revoke", "source_missing"])
@pytest.mark.parametrize("suffix", ["review-packet", "source", "frames/missing"])
def test_media_reads_recheck_permissions_and_source(
    http, config, media_scope, processed, change, suffix
):
    with media_jobs.connection(config) as db:
        if change in ("query", "original"):
            auth.set_grant(
                db,
                media_scope[0]["id"],
                media_scope[1],
                query=change != "query",
                original=change != "original",
                add=True,
            )
        elif change == "revoke":
            auth.revoke_client(db, media_scope[0]["id"])
        else:
            source = media_jobs.binding(media_jobs.load(db, media_scope[0]["id"], processed))[
                "source"
            ]
            relative = objects.object_relative(media_scope[1], source["sha256"])
            (config.state_root / relative).unlink()
    response = http.get(f"/admin/media/{processed}/{suffix}")
    assert response.status_code == (503 if change == "source_missing" else 404)
