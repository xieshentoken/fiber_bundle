"""Observable admin evidence packets: provenance never certifies platform completeness."""

import json

import pytest
from test_media_jobs import media_scope as media_scope
from test_media_processing import complete

from fiber_bundle import auth, media_jobs, media_links
from fiber_bundle.media_files import canonical

URL = "https://www.youtube.com/watch?v=BaW_jenozKc"


def source_context(http, config, scope, *, version=2, access="public_acquired"):
    job = complete(config, scope)
    with media_jobs.connection(config) as db:
        auth.set_grant(db, scope[0]["id"], scope[1], add=True, query=True, original=True)
        bound = media_jobs.binding(media_jobs.load(db, scope[0]["id"], job))
    identity = media_links.create(
        config,
        scope[0]["id"],
        scope[1],
        URL,
        "audio_only",
        3001 if version == 2 else 3000,
        "sha256:" + "a" * 64,
    )
    capture = {
        "schema": f"fiber-media-link-capture/v{version}",
        "url": URL,
        "mode": "audio_only",
        "declared_duration_ms": 3001 if version == 2 else 3000,
        "source_sha256": bound["source"]["sha256"],
        "source_bytes": bound["source"]["byte_count"],
        "media_type": "audio/wav",
        "access_state": access,
        "source_coverage_verified": False,
        "cookies_exported": False,
    }
    if version == 2:
        capture["converted_duration_ms"] = 3000
    stored = {
        "receipt_id": bound["source"]["receipt_id"],
        "asset_id": bound["source"]["asset_id"],
        "capture": capture,
    }
    with media_jobs.connection(config) as db:
        db.execute(
            "UPDATE media_link_tasks SET state='complete',job_id=?,binding_json=? WHERE id=?",
            (job, canonical(stored).decode(), identity),
        )
    return job, identity, stored


@pytest.mark.parametrize(
    "version,access", [(1, "public_acquired"), (2, "public_acquired"), (2, "browser_copy_attested")]
)
def test_packet_exposes_distinct_bound_durations_without_acceptance(
    http, config, media_scope, version, access
):
    job, _, stored = source_context(http, config, media_scope, version=version, access=access)
    response = http.get(f"/admin/media/{job}/review-packet")
    assert response.status_code == 200
    packet = response.json()
    context = packet["source_context"]
    assert context["source_sha256"] == packet["candidate"]["source_sha256"]
    assert context["source_bytes"] == stored["capture"]["source_bytes"]
    assert context["duration_ms"] == 3000
    platform = context["platform"]
    assert platform["url"] == URL
    assert platform["declared_duration_ms"] == (3001 if version == 2 else 3000)
    assert platform["converted_duration_ms"] == (3000 if version == 2 else None)
    assert platform["access_state"] == access
    assert not platform["source_coverage_verified"] and not platform["login_verified"]
    assert not packet["review_gate"]["publication_ready"]
    assert not packet["review_gate"]["human_review_recorded"]
    with media_jobs.connection(config) as db:
        assert db.execute("SELECT count(*) FROM media_reviews").fetchone()[0] == 0
    assert http.get("/static/media_evidence.js").status_code == 200


def test_direct_packet_has_no_invented_platform(http, config, media_scope):
    job = complete(config, media_scope)
    with media_jobs.connection(config) as db:
        auth.set_grant(
            db, media_scope[0]["id"], media_scope[1], add=True, query=True, original=True
        )
    response = http.get(f"/admin/media/{job}/review-packet")
    assert response.status_code == 200
    assert response.json()["source_context"]["platform"] is None


@pytest.mark.parametrize(
    "change",
    [
        "client",
        "vault",
        "receipt",
        "asset",
        "sha",
        "bytes",
        "url",
        "mode",
        "duration",
        "schema",
        "coverage",
        "cookies",
        "malformed",
        "duplicate",
    ],
)
def test_changed_origin_is_unavailable_not_mislabelled(http, config, media_scope, change):
    job, identity, stored = source_context(http, config, media_scope)
    other = None
    if change == "duplicate":
        other = media_links.create(
            config,
            media_scope[0]["id"],
            media_scope[1],
            URL,
            "audio_only",
            3001,
            "sha256:" + "a" * 64,
        )
    with media_jobs.connection(config) as db:
        if change in {"client", "vault", "mode"}:
            column, value = {
                "client": ("client_id", "different"),
                "vault": ("vault_id", "different"),
                "mode": ("mode", "full_evidence"),
            }[change]
            # Foreign keys remain enabled; use another real identity for ownership checks.
            if change == "client":
                value = auth.create_client(db, "other", False)["id"]
            if change == "vault":
                value = db.execute(
                    "SELECT id FROM vaults WHERE id!=?", (media_scope[1],)
                ).fetchone()[0]
            db.execute(f"UPDATE media_link_tasks SET {column}=? WHERE id=?", (value, identity))
        elif change == "duplicate":
            db.execute(
                "UPDATE media_link_tasks SET state='complete',job_id=?,binding_json=? WHERE id=?",
                (job, canonical(stored).decode(), other),
            )
        else:
            if change in {"receipt", "asset"}:
                stored[change + "_id"] = "different"
            elif change == "malformed":
                stored["capture"] = []
            else:
                field, value = {
                    "sha": ("source_sha256", "a" * 64),
                    "bytes": ("source_bytes", 1),
                    "url": ("url", "https://invalid.example/"),
                    "mode": ("mode", "full_evidence"),
                    "duration": ("converted_duration_ms", 2999),
                    "schema": ("schema", "unknown"),
                    "coverage": ("source_coverage_verified", True),
                    "cookies": ("cookies_exported", True),
                }[change]
                stored["capture"][field] = value
            db.execute(
                "UPDATE media_link_tasks SET binding_json=? WHERE id=?",
                (json.dumps(stored), identity),
            )
    response = http.get(f"/admin/media/{job}/review-packet")
    assert response.status_code == 503
    assert response.json() == {"error": "storage_unavailable"}


def test_revoked_original_permission_hides_origin(http, config, media_scope):
    job, _, _ = source_context(http, config, media_scope)
    with media_jobs.connection(config) as db:
        auth.set_grant(
            db, media_scope[0]["id"], media_scope[1], add=True, query=True, original=False
        )
    assert http.get(f"/admin/media/{job}/review-packet").status_code == 404
