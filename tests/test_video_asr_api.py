"""Real chunk/ledger/audit behavior with synthetic provider results; no paid calls."""

import json
from dataclasses import asdict

import pytest
from test_channel_intake import records
from test_intake_video_skill import install_v3

from fiber_bundle import (
    automatic_ingestion,
    database,
    video_asr_api,
    video_asr_transport,
    video_skill,
)
from fiber_bundle.channels import intake_extraction, intake_video_skill
from fiber_bundle.config import InvalidInput
from fiber_bundle.model_configuration import store as catalog
from fiber_bundle.model_configuration.contracts import Save
from fiber_bundle.model_configuration.errors import ModelError

pytest_plugins = [
    "test_channel_intake",
    "test_execution",
    "test_intake_video_skill",
    "test_model_configuration",
]


def configure_api(config, policy, monkeypatch):
    monkeypatch.setattr(
        "fiber_bundle.model_configuration.runtime.expected_image", lambda: policy.image
    )
    with database.connect(config.state_root) as db:
        model = catalog.save(
            config,
            db,
            Save(
                provider="custom",
                name="Synthetic ASR",
                base_url="https://asr.example.org/v1",
                model="custom-transcribe",
                api_key="synthetic-asr-key",
                budget_tokens=1000000,
            ),
        )
    value = video_skill.settings(config)
    value.update(asr_backend="transcription-api", asr_api_model_configuration_id=model["id"])
    (config.state_root / "configs/video-skill.json").write_text(json.dumps(value))
    return model


def current(config, entry, batch):
    return json.loads(next(r for r in records(config, entry) if r["batch_id"] == batch)["value"])[
        "links"
    ][0]


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_api_route_obeys_original_review_and_visual_empty_rules(
    config, entry, policy, monkeypatch, tmp_path, media, vault_keychain, empty, direct
):
    batch, steps = install_v3(
        config, entry, policy, monkeypatch, tmp_path, media, subtitles=False, direct=direct
    )
    model = configure_api(config, policy, monkeypatch)
    called = []

    def transcribe(selected, audio, prompt, language, format):
        assert selected.model == "custom-transcribe" and audio[:4] == b"RIFF"
        assert "API" in prompt and language == "auto"
        called.append(selected.configuration_id)
        return {
            "text": "" if empty else "Synthetic audio transcript explains the diagram.",
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }

    monkeypatch.setattr(video_asr_transport, "transcribe", transcribe)
    assert intake_extraction.advance(config, entry[0]["id"])
    link = current(config, entry, batch)
    assert link["state"] == "review_required", link
    frozen = link["video_processing"]
    assert frozen["transcript_route"] == ("visual_primary" if empty else "transcription_api")
    assert "run_asr" not in steps
    assert len(called) == 1
    job = config.state_root / "video-skill-jobs" / frozen["job_id"]
    assert link["metadata"]["audit_passed"]
    with database.connect(config.state_root) as db:
        row = db.execute("select * from model_configuration_calls").fetchone()
        assert row["state"] == "succeeded" and row["configuration_id"] == model["id"]
        assert (
            db.execute(
                "select charged from model_budget_reservations where id=?", (row["reservation_id"],)
            ).fetchone()[0]
            == 30
        )
    for _ in range(6):
        automatic_ingestion.tick(config)
    assert (
        current(config, entry, batch).get("publication", {}).get("state") == "published"
    ) is direct
    # Success cache and the same credential usage survive a repeat of the ASR stage.
    video_asr_api.transcribe(config, frozen, job, lambda: None, lambda: None) if not empty else None
    assert len(called) == 1


def test_api_failure_requires_explicit_new_attempt_keeps_prior_charge(
    config, entry, policy, monkeypatch, tmp_path, media, vault_keychain
):
    batch, _ = install_v3(config, entry, policy, monkeypatch, tmp_path, media, subtitles=False)
    configure_api(config, policy, monkeypatch)
    monkeypatch.setattr(
        video_asr_transport,
        "transcribe",
        lambda *a: (_ for _ in ()).throw(ModelError("model_timeout")),
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    link = current(config, entry, batch)
    assert link["state"] == "needs_review", link
    with database.connect(config.state_root) as db:
        before = [tuple(r) for r in db.execute("select * from model_budget_reservations")]
        with pytest.raises(InvalidInput, match="confirmation_required"):
            intake_video_skill.prepare_retry(db, link)
        assert link["video_processing"]["asr_runs"]
        intake_video_skill.prepare_retry(db, link, approve_new_attempt=True)
        assert not link["video_processing"]["asr_runs"]
        assert [tuple(r) for r in db.execute("select * from model_budget_reservations")] == before
        assert link["video_processing"]["previous_asr_runs"]


def test_missing_token_usage_preserves_reservation_and_never_reports_zero_tokens(
    config, store, policy, monkeypatch, tmp_path, vault_keychain
):
    from test_video_skill import configured

    configured(config, tmp_path)
    configure_api(config, policy, monkeypatch)
    frozen = {"skill": video_skill.freeze(config), "job_id": "synthetic-job"}
    job = config.state_root / "api-job"
    selected = video_asr_api.selected_policy(config, frozen["skill"]["settings"])
    monkeypatch.setattr(
        video_asr_transport,
        "transcribe",
        lambda *a: {"text": "audio text", "usage": {"type": "duration", "seconds": 30}},
    )
    video_asr_api.attempt(
        config, selected, frozen, job, "1", b"audio", "terms", lambda: None, lambda: None
    )
    with database.connect(config.state_root) as db:
        call = db.execute("select * from model_configuration_calls").fetchone()
        budget = db.execute(
            "select * from model_budget_reservations where id=?", (call["reservation_id"],)
        ).fetchone()
        assert (
            call["state"] == "succeeded"
            and call["input_tokens"] is None
            and call["charged_nusd"] is None
        )
        assert budget["state"] == "uncertain" and budget["charged"] is None
    path = job / "transcript/api-results" / (call["id"] + ".json")
    value = json.loads(path.read_text())
    value["result"]["text"] = "tampered"
    path.write_text(json.dumps(value))
    with pytest.raises(InvalidInput, match="result_changed"):
        video_asr_api.attempt(
            config, selected, frozen, job, "1", b"audio", "terms", lambda: None, lambda: None
        )


def test_only_audio_compatible_endpoints_selected(policy):
    from fiber_bundle.model_configuration.policy import RegisteredPolicy

    base = asdict(policy)
    for provider, endpoint in [
        ("anthropic", "https://asr.example.org/v1/messages"),
        ("custom", "https://asr.example.org/other"),
    ]:
        with pytest.raises(InvalidInput, match="protocol_unsupported"):
            video_asr_transport.endpoint(
                RegisteredPolicy(**{**base, "provider": provider, "endpoint": endpoint})
            )


def test_transport_real_local_multipart_and_no_redirect_credentials(
    config, store, policy, monkeypatch, tmp_path, vault_keychain
):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from fiber_bundle.model_configuration.policy import resolve
    from fiber_bundle.video_asr_transport import transcribe

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.path,
                    self.headers.get("Authorization"),
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            if self.path.endswith("/redirect/audio/transcriptions"):
                self.send_response(307)
                self.send_header("Location", "/leaked")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    b'{"text":"local audio","usage":{"input_tokens":2,"output_tokens":3}}'
                )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        "fiber_bundle.model_configuration.runtime.expected_image", lambda: policy.image
    )
    try:
        for path in ("v1", "redirect"):
            with database.connect(config.state_root) as db:
                model = catalog.save(
                    config,
                    db,
                    Save(
                        provider="local",
                        name="Synthetic local ASR",
                        base_url=f"http://127.0.0.1:{server.server_port}/{path}",
                        model="transcribe-test",
                        api_key="synthetic-local-only",
                    ),
                )
            selected = resolve(config, model["id"])
            if path == "redirect":
                with pytest.raises(ModelError):
                    transcribe(selected, b"RIFF-synthetic", "API, GPU", "zh", "json")
            else:
                assert (
                    transcribe(selected, b"RIFF-synthetic", "API, GPU", "zh", "json")["text"]
                    == "local audio"
                )
        assert len(received) == 2 and all(
            item[1] == "Bearer synthetic-local-only" for item in received
        )
        assert b'name="model"\r\n\r\ntranscribe-test' in received[0][2]
        assert b'filename="audio.wav"' in received[0][2]
        assert b'name="language"\r\n\r\nzh' in received[0][2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_api_budget_and_cancellation_do_not_repeat_charges(
    config, store, policy, monkeypatch, tmp_path, vault_keychain
):
    from test_video_skill import configured

    configured(config, tmp_path)
    model = configure_api(config, policy, monkeypatch)
    frozen = {"skill": video_skill.freeze(config), "job_id": "cancelled-job"}
    job = config.state_root / "cancelled-api"
    selected = video_asr_api.selected_policy(config, frozen["skill"]["settings"])
    cancelled = []

    def reply(*args):
        cancelled.append(True)
        return {"text": "spoken content", "usage": {"input_tokens": 5, "output_tokens": 4}}

    def guard():
        if cancelled:
            raise InvalidInput("cancelled")

    monkeypatch.setattr(video_asr_transport, "transcribe", reply)
    with pytest.raises(InvalidInput, match="cancelled"):
        video_asr_api.attempt(config, selected, frozen, job, "1", b"audio", "terms", guard, guard)
    with database.connect(config.state_root) as db:
        call = db.execute("select * from model_configuration_calls").fetchone()
        assert call["state"] == "succeeded" and call["input_tokens"] == 5
        assert (
            db.execute(
                "select charged from model_budget_reservations where id=?",
                (call["reservation_id"],),
            ).fetchone()[0]
            == 9
        )
    assert not (job / "output").exists()
    assert model["id"] == selected.configuration_id


def test_long_audio_recovery_reuses_successful_chunk_and_keeps_unknown_fee(
    config, store, policy, monkeypatch, tmp_path, vault_keychain
):
    import wave

    from test_video_skill import configured

    configured(config, tmp_path)
    configure_api(config, policy, monkeypatch)
    frozen = {"skill": video_skill.freeze(config), "job_id": "long-audio"}
    job = config.state_root / "long-api"
    (job / "transcript").mkdir(parents=True)
    (job / "metadata").mkdir()
    (job / "source").mkdir()
    (job / "source/video.mp4").write_bytes(b"synthetic-source")
    (job / "metadata/source_coverage.json").write_text(
        json.dumps(
            {"complete": True, "media_path": "source/video.mp4", "acquired_duration_seconds": 62}
        )
    )
    audio = job / "transcript/asr_audio.wav"
    with wave.open(str(audio), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        out.writeframes(b"\x00\x00" * 16000 * 62)
    (job / "transcript/asr_prompt.txt").write_text("ASR, API")
    monkeypatch.setattr(video_skill, "run", lambda *a, **kw: None)
    called = []

    def provider(*args):
        called.append(len(called) + 1)
        if len(called) == 2:
            raise ModelError("model_timeout")
        return {"text": "chunk text", "usage": {"input_tokens": 4, "output_tokens": 2}}

    monkeypatch.setattr(video_asr_transport, "transcribe", provider)
    with pytest.raises(ModelError):
        video_asr_api.transcribe(config, frozen, job, lambda: None, lambda: None)
    with database.connect(config.state_root) as db:
        previous = list(db.execute("select id,state from model_configuration_calls"))
        assert [r["state"] for r in previous] == ["succeeded", "failed"]
        with pytest.raises(InvalidInput, match="confirmation_required"):
            video_asr_api.prepare_retry(db, frozen, False)
        video_asr_api.prepare_retry(db, frozen, True)
    video_asr_api.transcribe(config, frozen, job, lambda: None, lambda: None)
    assert len(called) == 4  # first chunk reused; second repeated once; final 2 seconds once
    payload = json.loads((job / "transcript/asr_backend_raw.json").read_bytes())
    assert [(r["start"], r["end"]) for r in payload["segments"]] == [(0, 30), (30, 60), (60, 62)]
    with database.connect(config.state_root) as db:
        assert (
            db.execute(
                "select state from model_configuration_calls where id=?", (previous[1]["id"],)
            ).fetchone()[0]
            == "failed"
        )
        assert (
            db.execute(
                "select count(*) from model_budget_reservations where state='uncertain'"
            ).fetchone()[0]
            == 1
        )


def test_cached_api_reply_is_reconciled_after_restart_without_new_charge(
    config, store, policy, monkeypatch, tmp_path, vault_keychain
):
    from test_video_skill import configured

    configured(config, tmp_path)
    configure_api(config, policy, monkeypatch)
    frozen = {"skill": video_skill.freeze(config), "job_id": "restart-job"}
    job = config.state_root / "video-skill-jobs/restart-job"
    selected = video_asr_api.selected_policy(config, frozen["skill"]["settings"])
    called = []

    def provider(*args):
        called.append(True)
        return {"text": "cached audio", "usage": {"input_tokens": 5, "output_tokens": 3}}

    monkeypatch.setattr(video_asr_transport, "transcribe", provider)
    video_asr_api.attempt(
        config, selected, frozen, job, "1", b"audio", "terms", lambda: None, lambda: None
    )
    rid = frozen["asr_runs"]["1"]
    with database.connect(config.state_root) as db:
        db.execute("update model_configuration_calls set state='interrupted' where id=?", (rid,))
        video_asr_api.prepare_retry(db, frozen, False)
        assert frozen["asr_runs"]["1"] == rid
    video_asr_api.attempt(
        config, selected, frozen, job, "1", b"audio", "terms", lambda: None, lambda: None
    )
    assert len(called) == 1
    with database.connect(config.state_root) as db:
        assert db.execute("select count(*) from model_configuration_calls").fetchone()[0] == 1
        assert db.execute("select sum(charged) from model_budget_reservations").fetchone()[0] == 8


def test_active_api_chunk_is_running_not_a_failed_retry(
    config, store, policy, monkeypatch, tmp_path, vault_keychain
):
    from test_video_skill import configured

    from fiber_bundle.channels.intake_recovery_status import project

    configured(config, tmp_path)
    configure_api(config, policy, monkeypatch)
    frozen = {"skill": video_skill.freeze(config), "job_id": "active-job"}
    job = config.state_root / "video-skill-jobs/active-job"
    selected = video_asr_api.selected_policy(config, frozen["skill"]["settings"])

    def provider(*args):
        with database.connect(config.state_root) as db:
            value = project(db, {"state": "extracting", "video_processing": frozen})
            assert value["model_state"] == "running"
            assert value["requires_new_model_attempt"] is False
        return {"text": "audio result", "usage": {"input_tokens": 3, "output_tokens": 2}}

    monkeypatch.setattr(video_asr_transport, "transcribe", provider)
    video_asr_api.attempt(
        config, selected, frozen, job, "1", b"audio", "terms", lambda: None, lambda: None
    )
