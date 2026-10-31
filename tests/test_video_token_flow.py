"""The whole extraction flow shares a video budget without repeating cached ASR."""

import json

from test_intake_video_skill import install_v3
from test_video_asr_api import configure_api, current

from fiber_bundle import automatic_ingestion, database, model_gateway, video_asr_transport
from fiber_bundle import video_token_budget as budget
from fiber_bundle.channels import intake_actions, intake_extraction

pytest_plugins = [
    "test_channel_intake",
    "test_execution",
    "test_model_configuration",
    "test_intake_video_skill",
]


def test_asr_and_vision_share_total_then_resume_and_save_without_reset(
    config, entry, policy, monkeypatch, tmp_path, media, vault_keychain
):
    batch, steps = install_v3(
        config, entry, policy, monkeypatch, tmp_path, media, subtitles=False, direct=True
    )
    configure_api(config, policy, monkeypatch)
    path = config.state_root / "configs/video-skill.json"
    settings = json.loads(path.read_text())
    settings["max_video_tokens"] = 16384
    path.write_text(json.dumps(settings))
    calls = []
    monkeypatch.setattr(
        video_asr_transport,
        "transcribe",
        lambda *a: (
            calls.append("asr")
            or {
                "text": "Synthetic source transcript explains the mechanism.",
                "usage": {"input_tokens": 20, "output_tokens": 10},
            }
        ),
    )
    original = model_gateway.complete
    monkeypatch.setattr(
        model_gateway,
        "complete",
        lambda *a: (_ for _ in ()).throw(
            AssertionError("vision should not fit remaining video budget")
        ),
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    failed = current(config, entry, batch)
    assert failed["reason"] == "video_skill_token_budget_exhausted"
    with database.connect(config.state_root) as db:
        before = budget.status(db, budget.descriptor(failed["video_processing"]))
        assert before["actual_tokens"] == 30 and before["remaining_tokens"] == 16354
        original_call = dict(db.execute("select * from model_configuration_calls").fetchone())
    assert calls == ["asr"]
    start = len(steps)
    monkeypatch.setattr(model_gateway, "complete", original)
    revision = intake_actions.inspect(config, entry[0]["id"])["revision"]
    intake_extraction.retry(
        config,
        entry[0]["id"],
        batch,
        0,
        revision,
        max_video_tokens=500000,
        video_note_mode="direct",
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    completed = current(config, entry, batch)
    assert completed["state"] == "review_required", completed
    assert completed["video_processing"]["token_budget"]["limit"] == 500000
    assert calls == ["asr"]
    assert not any(
        s.startswith("acquire_public") or s in {"run_asr", "select_keyframes", "fiber_asr_api"}
        for s in steps[start:]
    )
    with database.connect(config.state_root) as db:
        actual = dict(db.execute("select * from model_configuration_calls").fetchone())
        assert actual == original_call
        total = budget.status(db, budget.descriptor(completed["video_processing"]))
        assert 30 < total["actual_tokens"] < 500000
    for _ in range(6):
        automatic_ingestion.tick(config)
    assert current(config, entry, batch)["publication"]["state"] == "published"


def test_refresh_keeps_explicit_zero_token_limit_before_first_model_call(
    config, entry, policy, monkeypatch, tmp_path, media
):
    from fiber_bundle import video_source_resolution
    from fiber_bundle.config import InvalidInput

    batch, _ = install_v3(config, entry, policy, monkeypatch, tmp_path, media)
    resolver = video_source_resolution.resolve
    monkeypatch.setattr(
        video_source_resolution,
        "resolve",
        lambda *a: (_ for _ in ()).throw(InvalidInput("intake_video_source_tls_interrupted")),
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    revision = intake_actions.inspect(config, entry[0]["id"])["revision"]
    intake_extraction.retry(
        config,
        entry[0]["id"],
        batch,
        0,
        revision,
        max_video_tokens=0,
        use_current_video_settings=True,
    )
    monkeypatch.setattr(video_source_resolution, "resolve", resolver)
    monkeypatch.setattr(
        model_gateway,
        "complete",
        lambda *a: (_ for _ in ()).throw(AssertionError("zero budget made a provider call")),
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    stopped = current(config, entry, batch)
    assert stopped["reason"] == "video_skill_token_budget_exhausted"
    assert stopped["video_processing"]["token_budget"]["limit"] == 0
    with database.connect(config.state_root) as db:
        assert db.execute("select count(*) from model_calls").fetchone()[0] == 0
