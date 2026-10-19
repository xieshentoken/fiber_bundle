import json
from unittest.mock import patch

import pytest
from test_media_jobs import media_scope as media_scope
from test_media_processing import complete

from fiber_bundle import (
    auth,
    media_cleanup,
    media_jobs,
    media_processing,
    media_processing_worker,
    media_publication,
    media_review,
    operations,
    publication_files,
)
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.media_files import canonical


def approved(config, scope, job):
    with media_jobs.connection(config) as db:
        auth.set_grant(db, scope[0]["id"], scope[1], add=True, query=True, original=True)
        value = media_processing.result_locked(config, db, scope[0]["id"], job)
        assessment = {
            **{
                k: value[k]
                for k in ("source_sha256", "result_sha256", "glossary_revision", "glossary_sha256")
            },
            "source_reviewed": True,
            "audio_reviewed": True,
            "visual_reviewed": bool(value["frames"]),
            "coverage_ranges": [{"start_ms": 0, "end_ms": value["duration_ms"]}],
            "judgments": [
                {
                    "unit_id": u["unit_id"],
                    "verdict": "accept",
                    "reason": "Synthetic fixture reviewed for this test.",
                }
                for u in media_review.units(value)
            ],
        }
        media_review.record(db, job, value, assessment)
    return value, assessment


def linked(config, scope, job, *, keep=False):
    # Persist actual origin schema without performing a network acquisition in unit tests.
    from fiber_bundle import media_links

    identity = media_links.create(
        config,
        scope[0]["id"],
        scope[1],
        "https://youtu.be/BaW_jenozKc",
        "audio_only",
        3000,
        "sha256:" + "a" * 64,
        keep_media=keep,
    )
    with media_jobs.connection(config) as db:
        source = media_jobs.binding(media_jobs.load(db, scope[0]["id"], job))["source"]
        binding = {
            "receipt_id": source["receipt_id"],
            "asset_id": source["asset_id"],
            "capture": {"access_state": "public_acquired"},
        }
        db.execute(
            "UPDATE media_link_tasks SET state='complete',job_id=?,binding_json=? WHERE id=?",
            (job, canonical(binding).decode(), identity),
        )
    return identity


def publish(config, job):
    with media_jobs.connection(config) as db:
        return media_publication.publish_locked(config, db, job)


@pytest.mark.parametrize("failure", ["coverage", "uncertain", "evidence", "unit", "visual"])
def test_review_rejects_incomplete_or_wrong_evidence(config, media_scope, failure):
    job = complete(config, media_scope)
    value, a = approved(config, media_scope, job)
    if failure == "coverage":
        a["coverage_ranges"][0]["end_ms"] -= 1
    elif failure == "uncertain":
        a["judgments"][0]["verdict"] = "uncertain"
    elif failure == "evidence":
        a["result_sha256"] = "a" * 64
    elif failure == "unit":
        a["judgments"].pop()
    else:
        a["visual_reviewed"] = True
    if failure in ("coverage", "uncertain"):
        assert not media_review.gate(value, a)["publication_ready"]
    else:
        with pytest.raises(InvalidInput):
            media_review.checked(value, a)


def test_explicit_publication_and_historical_note_edits_are_preserved(config, media_scope):
    job = complete(config, media_scope)
    with media_jobs.connection(config) as db:
        auth.set_grant(
            db, media_scope[0]["id"], media_scope[1], add=True, query=True, original=True
        )
    with pytest.raises(InvalidInput, match="review_required"):
        publish(config, job)
    value, a = approved(config, media_scope, job)
    linked(config, media_scope, job)
    result = publish(config, job)
    root = config.project_root / dict(config.vaults)["personal"] / result["relative_path"]
    assert "音频优先整理，未核验画面内容" in (root / "note.md").read_text()
    assert json.loads((root / "provenance.json").read_text())["platform_origin"]["url"].endswith(
        "BaW_jenozKc"
    )
    (root / "note.md").write_text("user edited")
    assert publish(config, job)["historical"] and (root / "note.md").read_text() == "user edited"
    with media_jobs.connection(config) as db:
        with pytest.raises(InvalidInput, match="frozen"):
            media_review.record(db, job, value, a)
        with pytest.raises(IntegrityError):
            media_cleanup.prepare_locked(config, db, job)
    (root / "note.md").unlink()
    assert publish(config, job)["historical"] and not (root / "note.md").exists()


def test_rename_interruption_is_explicitly_reconciled_without_overwrite(config, media_scope):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    rename = publication_files.rename_exclusive

    def after(*args):
        rename(*args)
        raise RuntimeError("synthetic crash after rename")

    with patch.object(publication_files, "rename_exclusive", side_effect=after):
        with pytest.raises(RuntimeError):
            publish(config, job)
    assert publish(config, job)["state"] == "published"


def test_existing_target_is_preserved_and_conflict_cannot_be_replayed(config, media_scope):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    root = (config.project_root / dict(config.vaults)["personal"]) / ("Media-" + job)
    root.mkdir()
    (root / "mine").write_text("keep")
    for _ in range(2):
        with pytest.raises(IntegrityError):
            publish(config, job)
    assert (root / "mine").read_text() == "keep"


@pytest.mark.parametrize("reason", ["direct", "keep", "shared"])
def test_cleanup_keeps_direct_shared_or_explicitly_retained_media(config, media_scope, reason):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    if reason != "direct":
        linked(config, media_scope, job, keep=reason == "keep")
    if reason == "shared":
        with media_jobs.connection(config) as db:
            import base64

            from fiber_bundle.payloads import Intake

            operations.intake(
                config,
                db,
                media_scope[0]["id"],
                Intake(
                    vault_id=media_scope[1],
                    event_id="another-original",
                    source_key="another-source",
                    session_id="synthetic",
                    raw_event=base64.b64encode(b"another-event").decode(),
                    assets=[
                        {
                            "role": "attachment",
                            "media_type": "audio/wav",
                            "original_name": "same.wav",
                            "data": base64.b64encode(media_scope[2][0][2]).decode(),
                        }
                    ],
                ),
            )
    publish(config, job)
    with media_jobs.connection(config) as db:
        with pytest.raises(InvalidInput):
            media_cleanup.prepare_locked(config, db, job)
    assert (media_processing_worker.directory(config, job) / "input/source").exists()


def test_cleanup_is_sha_confirmed_resumable_and_preserves_derived_evidence(config, media_scope):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    linked(config, media_scope, job)
    publish(config, job)
    root = media_processing_worker.directory(config, job)
    evidence = {p: p.read_bytes() for p in (root / "output").iterdir()}
    with media_jobs.connection(config) as db:
        plan = media_cleanup.prepare_locked(config, db, job)
        with pytest.raises(InvalidInput):
            media_cleanup.apply_locked(config, db, job, "a" * 64)
        unlink = __import__("os").unlink
        count = 0

        def crash(path, *a, **kw):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("synthetic deletion crash")
            return unlink(path, *a, **kw)

        with patch("fiber_bundle.media_cleanup.os.unlink", side_effect=crash):
            with pytest.raises(RuntimeError):
                media_cleanup.apply_locked(config, db, job, plan["confirmation_sha256"])
        resumed = media_cleanup.prepare_locked(config, db, job)
        assert (
            resumed["confirmation_sha256"] == plan["confirmation_sha256"]
            and resumed["state"] == "deleting"
        )
        assert (
            media_cleanup.apply_locked(config, db, job, plan["confirmation_sha256"])["state"]
            == "complete"
        )
        assert media_cleanup.apply_locked(config, db, job, plan["confirmation_sha256"])[
            "historical"
        ]
        assert media_cleanup.prepare_locked(config, db, job)["state"] == "complete"
        source = media_jobs.binding(media_jobs.load(db, media_scope[0]["id"], job))["source"]
        with pytest.raises(auth.Denied) as error:
            operations.original(
                config,
                db,
                media_scope[0]["id"],
                media_scope[1],
                source["version_id"],
                source["asset_id"],
                8 * 1024 * 1024,
            )
        assert error.value.status == 410
        listing = operations.versions(config, db, media_scope[0]["id"], media_scope[1], 0, 100)
        assert any(
            a.get("retention_state") == "cleaned_after_publication"
            for v in listing
            for a in v["assets"]
        )
    assert media_processing.status(config, media_scope[0]["id"], job)["state"] == "complete"
    value = media_processing.result(config, media_scope[0]["id"], job)
    assert value["historical"] and not value["source_bytes_verified"]
    assert all(p.read_bytes() == b for p, b in evidence.items())
    from fiber_bundle.store import Store

    assert Store(config).snapshot().exists()


def test_cleanup_replacement_after_plan_is_refused(config, media_scope):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    linked(config, media_scope, job)
    publish(config, job)
    with media_jobs.connection(config) as db:
        plan = media_cleanup.prepare_locked(config, db, job)
        path = media_processing_worker.directory(config, job) / "input/source"
        data = path.read_bytes()
        path.unlink()
        path.write_bytes(data)
        with pytest.raises(IntegrityError):
            media_cleanup.apply_locked(config, db, job, plan["confirmation_sha256"])
        assert path.exists()


def test_admin_cookie_csrf_and_owner_original_permissions(http, config, media_scope):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    assert http.get(f"/admin/media/{job}/review-packet").status_code == 200
    outsider = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient
    from fiber_bundle.api import create_app

    with outsider(
        create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 49000)
    ) as anonymous:
        assert anonymous.get(f"/admin/media/{job}/review-packet").status_code == 401
    csrf = http.headers.pop("x-csrf-token")
    assert http.post(f"/admin/media/{job}/publish").status_code == 403
    http.headers["x-csrf-token"] = csrf
    with media_jobs.connection(config) as db:
        auth.set_grant(
            db, media_scope[0]["id"], media_scope[1], add=True, query=True, original=False
        )
    assert http.get(f"/admin/media/{job}/review-packet").status_code == 404


def test_maximum_subtitle_units_can_all_be_reviewed(config, media_scope):
    job = complete(config, media_scope)
    value, a = approved(config, media_scope, job)
    value["transcript"]["cues"] = [
        {"start_ms": i * 30, "end_ms": (i + 1) * 30, "text": "literal"} for i in range(100)
    ]
    a["judgments"] = [
        {"unit_id": u["unit_id"], "verdict": "accept", "reason": "Synthetic unit limit test"}
        for u in media_review.units(value)
    ]
    assert len(a["judgments"]) == 101
    assert media_review.gate(value, a)["publication_ready"]
