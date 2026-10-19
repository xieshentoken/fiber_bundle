"""Opt-in actual Docker tests. Only generated silence/black-frame sources are ingested."""

import base64
import io
import os
import subprocess
import time
import wave
from pathlib import Path
from unittest.mock import patch

import pytest
from test_media_jobs import RAW

from fiber_bundle import auth, database, media_jobs, media_worker
from fiber_bundle.config import InvalidInput
from fiber_bundle.operations import intake
from fiber_bundle.payloads import Intake


@pytest.fixture
def docker_scope(config, store):
    image = os.environ.get("FIBER_TEST_MEDIA_IMAGE")
    if not image:
        pytest.skip("explicit pinned media image required")
    policy = media_worker.MediaPolicy(image)
    policy.validate()
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic-docker-media", False)
    vault = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vault, query=True, original=False, add=True)
    db.close()
    return client, vault, policy


def wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 24000)
    return buffer.getvalue()


def create(config, scope, *, video=False, raw=RAW, mode="audio_only", duration=3000, bad=False):
    client, vault, policy = scope
    source = Path(__file__).parent / "fixtures/m9/synthetic.mp4"
    data = source.read_bytes() if video else wav()
    if bad:
        data = b"corrupt synthetic media"
    mime = "video/mp4" if video else "audio/wav"
    assets = [
        {
            "role": "attachment",
            "media_type": mime,
            "original_name": "synthetic.bin",
            "data": base64.b64encode(data).decode(),
        }
    ]
    if raw is not None:
        assets.append(
            {
                "role": "attachment",
                "media_type": "text/vtt" if raw.startswith(b"WEBVTT") else "application/x-subrip",
                "original_name": "literal.txt",
                "data": base64.b64encode(raw).decode(),
            }
        )
    with media_jobs.connection(config) as db:
        receipt = intake(
            config,
            db,
            client["id"],
            Intake(
                vault_id=vault,
                event_id="media",
                source_key="synthetic",
                session_id="synthetic",
                raw_event=base64.b64encode(b"synthetic-event").decode(),
                assets=assets,
            ),
        )
        rows = db.execute(
            "SELECT asset_id,media_type FROM version_assets WHERE version_id=?",
            (receipt["source_version_id"],),
        ).fetchall()
    ids = {r["media_type"]: r["asset_id"] for r in rows}
    subtitle = next((a for m, a in ids.items() if m in {"text/vtt", "application/x-subrip"}), None)
    job = media_jobs.create(
        config,
        client["id"],
        receipt["job_id"],
        ids[mime],
        mode,
        duration,
        policy,
        subtitle_receipt_id=receipt["job_id"] if subtitle else None,
        subtitle_asset_id=subtitle,
    )
    return job, data


@pytest.mark.parametrize("kind", ["wav", "mp4_audio", "mp4_full", "vtt", "missing", "preview"])
def test_real_container_source_subtitle_and_retention(config, docker_scope, kind):
    raw = b"WEBVTT\n\n00:00.000 --> 00:03.000\nLiteral VTT caption.\n" if kind == "vtt" else RAW
    if kind == "missing":
        raw = None
    job, data = create(
        config,
        docker_scope,
        video=kind.startswith("mp4"),
        raw=raw,
        mode="full_evidence" if kind == "mp4_full" else "audio_only",
        duration=4000 if kind == "preview" else 3000,
    )
    cid = docker_scope[0]["id"]
    assert media_jobs.run(config, cid, job)["state"] == "complete"
    value = media_jobs.result(config, cid, job)
    assert value["probe"]["duration_ms"] == 3000
    assert value["state"] == (
        "blocked"
        if kind == "preview"
        else "pending"
        if kind in {"missing", "mp4_full"}
        else "candidate"
    )
    assert not value["publication_ready"] and not value["cleanup_allowed"]
    root = media_worker.directory(config, job)
    assert (root / "input/source").read_bytes() == data
    if raw is not None:
        assert (root / "input/subtitle").read_bytes() == raw
    assert (
        subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=^/fiber-media-" + job + "$"],
            check=True,
            capture_output=True,
        ).stdout.strip()
        == b""
    )


@pytest.mark.parametrize("kind", ["corrupt_wav", "corrupt_mp4", "bad_timestamp"])
def test_real_container_rejects_bad_media_or_subtitles_keeps_input(config, docker_scope, kind):
    raw = RAW.replace(b"03,000", b"04,000") if kind == "bad_timestamp" else RAW
    job, data = create(
        config, docker_scope, raw=raw, video=kind == "corrupt_mp4", bad=kind.startswith("corrupt")
    )
    cid = docker_scope[0]["id"]
    with pytest.raises(InvalidInput, match="worker_rejected"):
        media_jobs.run(config, cid, job)
    assert media_jobs.status(config, cid, job)["state"] == "failed"
    assert (media_worker.directory(config, job) / "input/source").read_bytes() == data
    with pytest.raises(InvalidInput):
        media_jobs.run(config, cid, job)


@pytest.mark.parametrize("kind", ["revoke", "cancel"])
def test_real_container_live_guard_stops_without_completion(config, docker_scope, kind):
    job, data = create(config, docker_scope)
    cid, vault = docker_scope[0]["id"], docker_scope[1]
    execute = media_worker.execute

    def controlled(*a, **kw):
        guard = kw["guard"]
        calls = 0

        def change():
            nonlocal calls
            calls += 1
            if calls == 4:  # After docker start; the original guard still checks live state.
                if kind == "revoke":
                    with media_jobs.connection(config) as db:
                        auth.set_grant(db, cid, vault, add=False, query=False, original=False)
                else:
                    media_jobs.stop(config, cid, job)
            guard()

        return execute(*a, **{**kw, "guard": change})

    with patch.object(media_worker, "execute", side_effect=controlled):
        with pytest.raises((auth.Denied, InvalidInput)):
            media_jobs.run(config, cid, job)
    with media_jobs.connection(config) as db:
        state = db.execute("SELECT state FROM media_tasks WHERE id=?", (job,)).fetchone()[0]
        assert state == ("failed" if kind == "revoke" else "cancelled")
    assert (media_worker.directory(config, job) / "input/source").read_bytes() == data


def test_real_container_late_delivery_is_timeout_and_retains_source(config, docker_scope):
    job, data = create(config, docker_scope)
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == 4:
            time.sleep(1.1)  # Simulate the host resuming after its deadline, after docker start.

    with pytest.raises(InvalidInput, match="media_worker_timeout"):
        media_worker.execute(
            config,
            media_worker.MediaPolicy(docker_scope[2].image, 1),
            job,
            data,
            RAW,
            {
                "format": "wav",
                "evidence_mode": "audio_only",
                "declared_duration_ms": 3000,
                "subtitle_kind": "srt",
            },
            guard=guard,
        )
    assert (media_worker.directory(config, job) / "input/source").read_bytes() == data
    assert (
        subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=^/fiber-media-" + job + "$"],
            check=True,
            capture_output=True,
        ).stdout.strip()
        == b""
    )
