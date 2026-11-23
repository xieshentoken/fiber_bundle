"""End-to-end retry quota, cache preservation and projected recovery status."""

import json

import pytest
from test_channel_intake import records
from test_intake_video_skill import install_v3

from fiber_bundle import database, model_gateway, video_call_allowance
from fiber_bundle.channels import intake_actions, intake_extraction
from fiber_bundle.config import InvalidInput

pytest_plugins = ["test_channel_intake", "test_execution"]


def test_zero_call_resume_pauses_then_bounded_retry_keeps_media(
    config, entry, policy, monkeypatch, tmp_path, media
):
    batch, steps = install_v3(config, entry, policy, monkeypatch, tmp_path, media)
    original = model_gateway.complete
    monkeypatch.setattr(
        model_gateway,
        "complete",
        lambda *a: (_ for _ in ()).throw(InvalidInput("video_skill_visual_failed")),
    )
    assert intake_extraction.advance(config, entry[0]["id"])

    def link():
        return json.loads(
            next(r for r in records(config, entry) if r["batch_id"] == batch)["value"]
        )["links"][0]

    revision = intake_actions.inspect(config, entry[0]["id"])["revision"]
    intake_extraction.retry(
        config,
        entry[0]["id"],
        batch,
        0,
        revision,
        approve_new_model_attempt=True,
        max_new_model_calls=0,
    )
    with pytest.raises(InvalidInput, match="already_queued"):
        intake_extraction.retry(config, entry[0]["id"], batch, 0, revision, max_new_model_calls=10)
    monkeypatch.setattr(
        model_gateway, "complete", lambda *a: pytest.fail("quota must stop before network")
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    stopped = link()
    assert stopped["reason"] == "video_skill_call_limit_reached"
    assert stopped["state"] == "needs_review" and "publication" not in stopped
    frozen = stopped["video_processing"]
    with database.connect(config.state_root) as db:
        assert video_call_allowance.status(db, frozen["call_allowance"])["used"] == 0
    start = len(steps)
    monkeypatch.setattr(model_gateway, "complete", original)
    intake_extraction.retry(config, entry[0]["id"], batch, 0, revision, max_new_model_calls=10)
    assert intake_extraction.advance(config, entry[0]["id"])
    completed = link()
    assert completed["state"] == "review_required", completed
    assert not any(
        s.startswith("acquire_public") or s in {"run_asr", "select_keyframes"}
        for s in steps[start:]
    )
    latest = completed["video_processing"]
    assert latest["previous_call_allowances"] == [frozen["call_allowance"]]
    with database.connect(config.state_root) as db:
        from fiber_bundle.video_progress import project

        progress = project(db, latest, config)
        assert progress["frames_complete"] == progress["frames_total"]
        assert 0 < progress["call_allowance"]["used"] <= 10


def test_refreshing_unstarted_snapshot_preserves_zero_call_allowance(
    config, entry, policy, monkeypatch, tmp_path, media
):
    from fiber_bundle import video_source_resolution

    batch, _ = install_v3(config, entry, policy, monkeypatch, tmp_path, media)
    original = video_source_resolution.resolve
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
        use_current_video_settings=True,
        max_new_model_calls=0,
    )
    monkeypatch.setattr(video_source_resolution, "resolve", original)
    monkeypatch.setattr(model_gateway, "complete", lambda *a: pytest.fail("refresh bypassed quota"))
    assert intake_extraction.advance(config, entry[0]["id"])
    link = json.loads(next(r for r in records(config, entry) if r["batch_id"] == batch)["value"])[
        "links"
    ][0]
    assert link["reason"] == "video_skill_call_limit_reached"
    assert link["video_processing"]["call_allowance"]["limit"] == 0


def test_legacy_failed_pi_can_continue_direct_without_new_pi_calls(
    config, entry, policy, monkeypatch, tmp_path, media
):
    from fiber_bundle import execution, execution_ledger

    batch, steps = install_v3(config, entry, policy, monkeypatch, tmp_path, media, legacy_pi=True)

    def failed(c, p, rid, prompt):
        with database.connect(c.state_root) as db:
            execution_ledger.start(c, db, rid, p)
            execution_ledger.transition(db, rid, "failed", "processing_model_incomplete")

    monkeypatch.setattr(execution, "execute", failed)
    assert intake_extraction.advance(config, entry[0]["id"])

    def link():
        return json.loads(
            next(r for r in records(config, entry) if r["batch_id"] == batch)["value"]
        )["links"][0]

    old = link()["video_processing"]
    revision = intake_actions.inspect(config, entry[0]["id"])["revision"]
    intake_extraction.retry(
        config, entry[0]["id"], batch, 0, revision, video_note_mode="direct", max_new_model_calls=0
    )
    monkeypatch.setattr(execution, "execute", lambda *a: pytest.fail("new Pi"))
    monkeypatch.setattr(model_gateway, "complete", lambda *a: pytest.fail("new vision"))
    assert intake_extraction.advance(config, entry[0]["id"])
    latest = link()
    assert latest["state"] == "review_required", latest
    assert latest["video_processing"]["note_runs"] == old["note_runs"]
    assert latest["video_processing"]["vision_runs"] == old["vision_runs"]
