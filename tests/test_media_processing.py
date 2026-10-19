import copy
from pathlib import Path
from unittest.mock import patch

import pytest
from test_media_glossary import SNAPSHOT, confirm, freeze
from test_media_jobs import create, fixture_worker
from test_media_jobs import media_scope as media_scope

from fiber_bundle import auth, media_jobs, media_processing, media_processing_worker
from fiber_bundle.config import IntegrityError, InvalidInput, make_directory
from fiber_bundle.media_files import canonical
from fiber_bundle.media_processing_capture import candidate
from fiber_bundle.media_processing_files import write
from fiber_bundle.media_subtitles import normalized
from fiber_bundle.media_transcript import convert

RESOURCE = {
    "image": "sha256:" + "a" * 64,
    "model_lock_sha256": "b" * 64,
    "timeout_seconds": 30,
    "memory_mib": 6144,
    "cpus": 2,
    "max_duration_ms": 60000,
    "daily_maximum": 2,
    "asr_enabled": False,
    "qualified": False,
}


def ready(config, scope, **options):
    job = create(config, scope, **options)
    with patch("fiber_bundle.media_worker.execute", side_effect=fixture_worker):
        media_jobs.run(config, scope[0]["id"], job)
    v = freeze(config, scope, job)
    confirm(config, scope, job, v)
    return job


def processing_worker(config, job, source, subtitle, spec, *, guard):
    guard()
    root = media_processing_worker.directory(config, job)
    make_directory(root / "input")
    make_directory(root / "output")
    write(root / "input", "source", source)
    write(root / "input", "request.json", canonical(spec))
    if subtitle:
        write(root / "input", "subtitle", subtitle)
    raw = (
        [
            {
                "start_ms": 0,
                "end_ms": 3000,
                "text": "fibre synthetic text",
                "language": "English",
                "state": "complete",
            }
        ]
        if spec["asr"]
        else None
    )
    value = candidate(spec, raw, subtitle, [])
    write(root / "output", "result.json", canonical(value))
    if raw is not None:
        write(root / "output", "raw.json", canonical({"chunks": raw}))
    if value["transcript"]["cues"]:
        write(root / "output", "normalized.srt", normalized(value["transcript"]["cues"]))
    guard()
    return value


def complete(config, scope):
    job = ready(config, scope)
    media_processing.create(config, scope[0]["id"], job, RESOURCE)
    with patch.object(media_processing_worker, "execute", side_effect=processing_worker):
        media_processing.run(config, scope[0]["id"], job)
    return job


def test_supplied_subtitle_is_not_replaced_and_original_result_is_preserved(config, media_scope):
    job = ready(config, media_scope)
    cid = media_scope[0]["id"]
    parent = Path(config.state_root / "media-tasks" / job)
    from fiber_bundle.media_worker import directory

    parent = directory(config, job)
    before = {p: p.read_bytes() for p in parent.rglob("*") if p.is_file()}
    media_processing.create(config, cid, job, RESOURCE)
    with patch.object(media_processing_worker, "execute", side_effect=processing_worker):
        media_processing.run(config, cid, job)
    with media_jobs.connection(config) as db:
        auth.set_grant(db, cid, media_scope[1], add=False, query=True, original=False)
    value = media_processing.result(config, cid, job)
    assert (
        value["complete"] and value["transcript"]["corrections"] == [] and value["model_calls"] == 0
    )
    assert not value["publication_ready"] and all(
        p.read_bytes() == data for p, data in before.items()
    )
    with pytest.raises(auth.Denied):
        media_processing.run(config, cid, job)


def test_asr_requires_actual_runtime_allowlist_and_explicit_resource_policy(config, media_scope):
    job = ready(config, media_scope, subtitle_receipt_id=None, subtitle_asset_id=None)
    with pytest.raises(InvalidInput):
        media_processing.create(config, media_scope[0]["id"], job, RESOURCE)
    resource = {**RESOURCE, "asr_enabled": True, "qualified": True}
    with pytest.raises(InvalidInput, match="runtime_unqualified"):
        media_processing.create(config, media_scope[0]["id"], job, resource)


def test_confirmation_change_stops_processing_and_never_replays(config, media_scope):
    job = ready(config, media_scope)
    cid = media_scope[0]["id"]
    media_processing.create(config, cid, job, RESOURCE)
    freeze(config, media_scope, job, canonical({**SNAPSHOT, "topic": "changed"}))
    with pytest.raises(IntegrityError):
        media_processing.run(config, cid, job)
    with pytest.raises(InvalidInput, match="no_replay"):
        media_processing.create(config, cid, job, RESOURCE)


@pytest.mark.parametrize("change", ["cancel", "revoke", "glossary", "source"])
def test_stopped_boundaries_preserve_failed_or_cancelled_attempt(config, media_scope, change):
    job = ready(config, media_scope)
    cid = media_scope[0]["id"]
    media_processing.create(config, cid, job, RESOURCE)

    def stopped(config, job, source, subtitle, spec, *, guard):
        if change == "cancel":
            with media_jobs.connection(config) as db:
                db.execute("UPDATE media_processing SET state='cancelled' WHERE job_id=?", (job,))
        elif change == "revoke":
            with media_jobs.connection(config) as db:
                auth.set_grant(db, cid, media_scope[1], query=False, original=False, add=False)
        elif change == "glossary":
            freeze(config, media_scope, job, canonical({**SNAPSHOT, "topic": "changed"}))
        else:
            with media_jobs.connection(config) as db:
                db.execute(
                    "UPDATE receipts SET state='failed' WHERE job_id=?", (media_scope[2][0][0],)
                )
        guard()

    with patch.object(media_processing_worker, "execute", side_effect=stopped):
        with pytest.raises((InvalidInput, IntegrityError, auth.Denied)):
            media_processing.run(config, cid, job)
    with media_jobs.connection(config) as db:
        row = media_processing.get(db, job)
        assert row["state"] in ("failed", "cancelled")
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


def test_global_daily_reservations_include_failed_attempts(config, media_scope):
    for _ in range(2):
        job = ready(config, media_scope)
        media_processing.create(config, media_scope[0]["id"], job, RESOURCE)
        with media_jobs.connection(config) as db:
            db.execute("UPDATE media_processing SET state='failed' WHERE job_id=?", (job,))
    job = ready(config, media_scope)
    with pytest.raises(InvalidInput, match="daily_budget"):
        media_processing.create(config, media_scope[0]["id"], job, RESOURCE)


def test_raw_asr_is_preserved_and_normalization_has_separate_corrections():
    raw = [
        {
            "start_ms": 0,
            "end_ms": 3000,
            "text": "A fibre sample",
            "language": "English",
            "state": "complete",
        }
    ]
    original = copy.deepcopy(raw)
    value = convert(raw, SNAPSHOT, 3000)
    assert raw == original and value["cues"][0]["text"] == "A Fiber sample"
    assert value["corrections"][0]["original"] == "A fibre sample"
    assert (
        not value["word_alignment_verified"] and value["timestamp_basis"] == "audio_chunk_interval"
    )
    g = copy.deepcopy(SNAPSHOT)
    g["terms"][0]["aliases"][0]["mode"] = "contextual"
    g["terms"][0]["context_keywords"] = ["biology"]
    value = convert(raw, g, 3000)
    assert value["ambiguities"] and value["cues"][0]["text"] == "A fibre sample"


def test_output_tampering_is_rejected(config, media_scope):
    job = complete(config, media_scope)
    cid = media_scope[0]["id"]
    with media_jobs.connection(config) as db:
        auth.set_grant(db, cid, media_scope[1], query=True, add=True, original=False)
    path = media_processing_worker.directory(config, job) / "output/normalized.srt"
    path.write_bytes(b"changed")
    with pytest.raises(InvalidInput):
        media_processing.result(config, cid, job)


def test_model_call_count_matches_transcribed_chunks():
    import hashlib

    glossary = canonical(SNAPSHOT)
    spec = {
        "schema": "fiber-media-processing-plan/v1",
        "parent_result_sha256": "a" * 64,
        "source_sha256": "b" * 64,
        "source_bytes": 12,
        "request": {
            "format": "wav",
            "evidence_mode": "audio_only",
            "declared_duration_ms": 3000,
            "subtitle_kind": None,
        },
        "duration_ms": 3000,
        "glossary_revision": 1,
        "glossary_sha256": hashlib.sha256(glossary).hexdigest(),
        "glossary": SNAPSHOT,
        "policy": {**RESOURCE, "asr_enabled": True, "qualified": True},
        "asr": True,
    }
    raw = [
        {
            "start_ms": 0,
            "end_ms": 1500,
            "text": "first",
            "language": "English",
            "state": "complete",
        },
        {
            "start_ms": 1500,
            "end_ms": 3000,
            "text": "second",
            "language": "English",
            "state": "complete",
        },
    ]
    assert candidate(spec, raw, None, [])["model_calls"] == 2
