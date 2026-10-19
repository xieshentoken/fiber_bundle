"""GUI-facing owner glossary transport preserves exact bytes, grants and explicit approval."""

import pytest
from test_media_glossary import RAW, SNAPSHOT
from test_media_jobs import create
from test_media_jobs import media_scope as media_scope

from fiber_bundle import auth, media_glossary, media_jobs
from fiber_bundle.media_files import canonical


def endpoint(job):
    return f"/v1/media/{job}/glossary"


def headers(scope):
    return {"Authorization": "Bearer " + scope[0]["token"]}


def confirmation(value, **extra):
    return {"revision": value["revision"], "snapshot_sha256": value["snapshot_sha256"], **extra}


def test_http_freeze_show_confirm_preserves_bytes_and_does_not_dispatch(http, config, media_scope):
    cid, vault = media_scope[0]["id"], media_scope[1]
    job = create(config, media_scope)
    with media_jobs.connection(config) as db:
        auth.set_grant(db, cid, vault, add=True, query=True, original=False)
    response = http.post(endpoint(job), content=RAW, headers=headers(media_scope))
    assert response.status_code == 200
    frozen = response.json()
    assert not frozen["confirmation_recorded"]
    shown = http.get(endpoint(job), headers=headers(media_scope))
    assert shown.status_code == 200 and shown.json()["snapshot"] == SNAPSHOT
    confirmed = http.post(
        endpoint(job) + "/confirm", json=confirmation(frozen), headers=headers(media_scope)
    )
    assert confirmed.status_code == 200 and confirmed.json()["terminology_gate_passed"]
    assert not any(
        confirmed.json()[key]
        for key in [
            "asr_ready",
            "asr_started",
            "semantic_review_verified",
            "publication_ready",
            "cleanup_allowed",
        ]
    )
    again = http.post(endpoint(job), content=canonical(SNAPSHOT), headers=headers(media_scope))
    assert again.json() == confirmed.json()
    with media_jobs.connection(config) as db:
        assert media_glossary.latest(db, job)["raw_json"].encode() == RAW
        assert db.execute("SELECT count(*) FROM media_glossary_revisions").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM media_glossary_confirmations").fetchone()[0] == 1
        for table in [
            "media_processing",
            "media_reviews",
            "media_publications",
            "media_cleanup",
            "model_calls",
        ]:
            assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0


@pytest.mark.parametrize("query,add", [(q, a) for q in [False, True] for a in [False, True]])
def test_http_independent_grants(http, config, media_scope, query, add):
    job = create(config, media_scope)
    frozen = media_glossary.freeze(config, media_scope[0]["id"], job, RAW)
    with media_jobs.connection(config) as db:
        auth.set_grant(
            db, media_scope[0]["id"], media_scope[1], add=add, query=query, original=False
        )
    assert http.get(endpoint(job), headers=headers(media_scope)).status_code == (
        200 if query else 404
    )
    assert http.post(endpoint(job), content=RAW, headers=headers(media_scope)).status_code == (
        200 if add else 404
    )
    assert http.post(
        endpoint(job) + "/confirm", json=confirmation(frozen), headers=headers(media_scope)
    ).status_code == (200 if add else 404)
    with media_jobs.connection(config) as db:
        assert db.execute("SELECT count(*) FROM media_glossary_confirmations").fetchone()[0] == int(
            add
        )


def test_no_bearer_other_owner_revoked_client_and_foreign_origin(http, config, media_scope):
    job = create(config, media_scope)
    # Existing administrator cookie alone is not an owner's terminology confirmation.
    assert http.post(endpoint(job), content=RAW).status_code == 401
    with media_jobs.connection(config) as db:
        other = auth.create_client(db, "other-http-glossary-owner", False)
        auth.set_grant(db, other["id"], media_scope[1], query=True, original=True, add=True)
    foreign = {"Authorization": "Bearer " + other["token"]}
    assert http.get(endpoint(job), headers=foreign).status_code == 404
    assert http.post(endpoint(job), content=RAW, headers=foreign).status_code == 404
    assert (
        http.post(
            endpoint(job),
            content=RAW,
            headers={**headers(media_scope), "Origin": "https://foreign.invalid"},
        ).status_code
        == 403
    )
    with media_jobs.connection(config) as db:
        auth.revoke_client(db, media_scope[0]["id"])
    assert http.post(endpoint(job), content=RAW, headers=headers(media_scope)).status_code == 401
    with media_jobs.connection(config) as db:
        assert db.execute("SELECT count(*) FROM media_glossary_revisions").fetchone()[0] == 0


def test_http_change_and_aba_reject_stale_confirmation(http, config, media_scope):
    job = create(config, media_scope)

    def freeze(raw):
        r = http.post(endpoint(job), content=raw, headers=headers(media_scope))
        assert r.status_code == 200
        return r.json()

    first = freeze(RAW)
    second = freeze(canonical({**SNAPSHOT, "topic": "Changed"}))
    third = freeze(RAW)
    assert (first["revision"], second["revision"], third["revision"]) == (1, 2, 3)
    assert third["snapshot_sha256"] == first["snapshot_sha256"]
    for value in [first, second, {**third, "snapshot_sha256": "f" * 64}]:
        response = http.post(
            endpoint(job) + "/confirm", json=confirmation(value), headers=headers(media_scope)
        )
        assert response.status_code == 422 and response.json() == {"error": "invalid_input"}
    assert (
        http.post(
            endpoint(job) + "/confirm", json=confirmation(third), headers=headers(media_scope)
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema":"fiber-media-glossary/v1","topic":"a","topic":"b","terms":[],"unresolved":[]}',
        b'{"schema":"fiber-media-glossary/v1","topic":NaN,"terms":[],"unresolved":[]}',
        b"not JSON",
        b"{}",
        b" " * (65536 + 1),
    ],
)
def test_invalid_raw_body_never_freezes_or_echoes_input(http, config, media_scope, raw):
    job = create(config, media_scope)
    response = http.post(endpoint(job), content=raw, headers=headers(media_scope))
    assert response.status_code == 422 and response.json() == {"error": "invalid_input"}
    with media_jobs.connection(config) as db:
        assert db.execute("SELECT count(*) FROM media_glossary_revisions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"revision": True},
        {"revision": "1"},
        {"revision": 0},
        {"snapshot_sha256": "bad"},
        {"allow_empty": "true"},
        {"client_id": "invented"},
    ],
)
def test_confirmation_payload_is_strict(http, config, media_scope, patch):
    job = create(config, media_scope)
    frozen = media_glossary.freeze(config, media_scope[0]["id"], job, RAW)
    response = http.post(
        endpoint(job) + "/confirm",
        json={**confirmation(frozen), **patch},
        headers=headers(media_scope),
    )
    assert response.status_code == 422
    with media_jobs.connection(config) as db:
        assert db.execute("SELECT count(*) FROM media_glossary_confirmations").fetchone()[0] == 0


@pytest.mark.parametrize("state", ["running", "failed", "cancelled", "interrupted"])
def test_stopped_task_rejects_mutation(http, config, media_scope, state):
    job = create(config, media_scope)
    frozen = media_glossary.freeze(config, media_scope[0]["id"], job, RAW)
    with media_jobs.connection(config) as db:
        db.execute("UPDATE media_tasks SET state=? WHERE id=?", (state, job))
    assert http.post(endpoint(job), content=RAW, headers=headers(media_scope)).status_code == 422
    assert (
        http.post(
            endpoint(job) + "/confirm", json=confirmation(frozen), headers=headers(media_scope)
        ).status_code
        == 422
    )


def test_ambiguity_and_empty_require_explicit_decision(http, config, media_scope):
    job = create(config, media_scope)
    raw = canonical({**SNAPSHOT, "unresolved": [{"term": "unknown", "reason": "Review later"}]})
    frozen = http.post(endpoint(job), content=raw, headers=headers(media_scope)).json()
    assert frozen["unresolved_count"] == 1
    assert (
        http.post(
            endpoint(job) + "/confirm", json=confirmation(frozen), headers=headers(media_scope)
        ).status_code
        == 422
    )
    empty = http.post(
        endpoint(job), content=canonical({**SNAPSHOT, "terms": []}), headers=headers(media_scope)
    ).json()
    assert (
        http.post(
            endpoint(job) + "/confirm", json=confirmation(empty), headers=headers(media_scope)
        ).status_code
        == 422
    )
    assert (
        http.post(
            endpoint(job) + "/confirm",
            json=confirmation(empty, allow_empty=True),
            headers=headers(media_scope),
        ).status_code
        == 200
    )
