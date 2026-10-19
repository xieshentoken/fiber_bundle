"""Full V3 frames/crops with the explicit visual-primary audit and existing publication."""

import json

import pytest
from test_channel_intake import records
from test_intake_video_skill import install_v3

from fiber_bundle import automatic_ingestion, database, model_gateway, video_skill
from fiber_bundle.channels import intake_extraction
from fiber_bundle.config import InvalidInput
from fiber_bundle.video_skill_notes import export
from fiber_bundle.video_visual import DISCLOSURE

pytest_plugins = ["test_channel_intake", "test_execution", "test_intake_video_skill"]


def link_for(config, entry, batch):
    record = next(r for r in records(config, entry) if r["batch_id"] == batch)
    return record, json.loads(record["value"])["links"][0]


@pytest.mark.parametrize("direct", [False, True])
def test_visual_primary_obeys_review_policy_and_discloses_unverified_speech(
    config, entry, policy, monkeypatch, tmp_path, media, direct
):
    batch, _ = install_v3(
        config,
        entry,
        policy,
        monkeypatch,
        tmp_path,
        media,
        subtitles=False,
        direct=direct,
        empty_asr=True,
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    _, link = link_for(config, entry, batch)
    assert link["state"] == "review_required", link
    frozen = link["video_processing"]
    assert frozen["transcript_route"] == "visual_primary"
    job = config.state_root / "video-skill-jobs" / frozen["job_id"]
    assert not list((job / "transcript").glob("*.srt"))
    assert DISCLOSURE in (job / "note/note.md").read_text()
    assert (
        json.loads((job / "metadata/audit_final.json").read_text())["schema"]
        == "fiber-video-visual-primary-audit/v1"
    )
    files = export(job, frozen)
    provenance = json.loads(files["video-provenance.json"])
    assert provenance["transcript"] is None and not provenance["visual_fallback"]["speech_verified"]
    for _ in range(6):
        automatic_ingestion.tick(config)
    _, link = link_for(config, entry, batch)
    assert (link.get("publication", {}).get("state") == "published") is direct
    if direct:
        path = config.vault_path("test vault/temp_vault1") / link["publication"]["entry_note_path"]
        assert DISCLOSURE in path.read_text()
    before = (job / "transcript/asr_diagnostic.json").read_bytes()
    assert not intake_extraction.advance(config, entry[0]["id"])
    assert (job / "transcript/asr_diagnostic.json").read_bytes() == before


@pytest.mark.parametrize(
    "reason", ["video_skill_run_asr_failed", "video_skill_run_asr_timeout", "video_skill_asr_empty"]
)
def test_missing_diagnostic_or_runtime_failure_never_calls_vision(
    config, entry, policy, monkeypatch, tmp_path, media, reason
):
    batch, _ = install_v3(config, entry, policy, monkeypatch, tmp_path, media, subtitles=False)
    original = video_skill.run

    def run(c, b, j, n, args, guard, **kwargs):
        if n == "run_asr":
            raise InvalidInput(reason)
        return original(c, b, j, n, args, guard, **kwargs)

    monkeypatch.setattr(video_skill, "run", run)
    monkeypatch.setattr(
        model_gateway, "complete", lambda *a: pytest.fail("invalid fallback invoked a cloud model")
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    _, link = link_for(config, entry, batch)
    assert link["state"] == "needs_review" and "publication" not in link
    assert link["reason"] == (
        "video_skill_visual_fallback_invalid" if reason == "video_skill_asr_empty" else reason
    )


def test_visual_retry_retains_media_ASR_and_old_charged_calls(
    config, entry, policy, monkeypatch, tmp_path, media
):
    from fiber_bundle.model_configuration.errors import ModelError

    batch, steps = install_v3(
        config, entry, policy, monkeypatch, tmp_path, media, subtitles=False, empty_asr=True
    )
    provider = model_gateway.complete
    monkeypatch.setattr(
        model_gateway, "complete", lambda *a: (_ for _ in ()).throw(ModelError("model_timeout"))
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    record, link = link_for(config, entry, batch)
    assert link["state"] == "needs_review"
    old_runs = dict(link["video_processing"]["vision_runs"])
    with pytest.raises(InvalidInput, match="new_attempt_confirmation_required"):
        intake_extraction.retry(config, entry[0]["id"], batch, 0, record["revision"])
    monkeypatch.setattr(model_gateway, "complete", provider)
    intake_extraction.retry(
        config, entry[0]["id"], batch, 0, record["revision"], approve_new_model_attempt=True
    )
    before = len(steps)
    assert intake_extraction.advance(config, entry[0]["id"])
    _, link = link_for(config, entry, batch)
    assert link["state"] == "review_required", link
    assert "run_asr" not in steps[before:] and "acquire_public:media" not in steps[before:]
    with database.connect(config.state_root) as db:
        for rid in old_runs.values():
            assert db.execute("SELECT 1 FROM execution_runs WHERE id=?", (rid,)).fetchone()


@pytest.mark.parametrize("tamper", ["disclosure", "frame_bytes", "segment_analysis"])
def test_changed_evidence_or_missing_disclosure_cannot_pass_visual_final_audit(
    config, entry, policy, monkeypatch, tmp_path, media, tamper
):
    real_run = video_skill.run
    batch, _ = install_v3(
        config,
        entry,
        policy,
        monkeypatch,
        tmp_path,
        media,
        subtitles=False,
        direct=False,
        empty_asr=True,
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    _, link = link_for(config, entry, batch)
    frozen = link["video_processing"]
    job = config.state_root / "video-skill-jobs" / frozen["job_id"]
    if tamper == "disclosure":
        path = job / "note/note.md"
        path.write_text(path.read_text().replace(DISCLOSURE, ""))
    elif tamper == "frame_bytes":
        frame = json.loads((job / "frames/frame_manifest.json").read_text())["frames"][0]
        with (job / frame["selected_path"]).open("ab") as stream:
            stream.write(b"changed")
    else:
        path = job / "evidence/segments.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        rows[0]["frames"][0]["analysis"]["summary"] = "changed"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    # Exercise the actual owned audit subprocess; no host test stub can pass it.
    with pytest.raises(InvalidInput, match="visual_audit_failed"):
        real_run(config, frozen["skill"], job, "fiber_visual", ["final"], lambda: None)


def test_no_useful_visual_information_does_not_create_a_note(
    config, entry, policy, monkeypatch, tmp_path, media
):
    batch, _ = install_v3(
        config, entry, policy, monkeypatch, tmp_path, media, subtitles=False, empty_asr=True
    )
    provider = model_gateway.complete

    def empty_picture(*args):
        result = provider(*args)
        body = json.loads(result["choices"][0]["message"]["content"])
        body.update(
            ocr_text=[],
            claims=[],
            illustrations=[],
            analysis_status="not_needed",
            skip_reason_code="no_unique_visual",
            review_note="Synthetic blank frame",
        )
        result["choices"][0]["message"]["content"] = json.dumps(body)
        return result

    monkeypatch.setattr(model_gateway, "complete", empty_picture)
    assert intake_extraction.advance(config, entry[0]["id"])
    _, link = link_for(config, entry, batch)
    assert link["reason"] == "video_skill_visual_content_missing" and "publication" not in link
    assert not link["video_processing"].get("note_runs")


def test_rejected_empty_vision_can_retry_with_confirmation_and_keeps_old_results(
    config, entry, policy, monkeypatch, tmp_path, media
):
    from fiber_bundle.channels.intake_recovery_status import project

    batch, steps = install_v3(
        config, entry, policy, monkeypatch, tmp_path, media, subtitles=False, empty_asr=True
    )
    provider = model_gateway.complete

    def empty_picture(*args):
        result = provider(*args)
        body = json.loads(result["choices"][0]["message"]["content"])
        body.update(
            ocr_text=[],
            claims=[],
            illustrations=[],
            analysis_status="not_needed",
            skip_reason_code="no_unique_visual",
            review_note="Synthetic blank frame",
        )
        result["choices"][0]["message"]["content"] = json.dumps(body)
        return result

    monkeypatch.setattr(model_gateway, "complete", empty_picture)
    assert intake_extraction.advance(config, entry[0]["id"])
    record, link = link_for(config, entry, batch)
    old_runs = link["video_processing"]["vision_runs"].copy()
    with database.connect(config.state_root) as db:
        assert project(db, link)["requires_new_model_attempt"]
    with pytest.raises(InvalidInput, match="new_attempt_confirmation_required"):
        intake_extraction.retry(config, entry[0]["id"], batch, 0, record["revision"])
    monkeypatch.setattr(model_gateway, "complete", provider)
    intake_extraction.retry(
        config, entry[0]["id"], batch, 0, record["revision"], approve_new_model_attempt=True
    )
    before = len(steps)
    assert intake_extraction.advance(config, entry[0]["id"])
    _, link = link_for(config, entry, batch)
    assert link["state"] == "review_required", link
    assert "run_asr" not in steps[before:] and "acquire_public:media" not in steps[before:]
    job = config.state_root / "video-skill-jobs" / link["video_processing"]["job_id"]
    assert len(list((job / "visual/previous").glob("*.json"))) == len(old_runs)
    with database.connect(config.state_root) as db:
        for rid in old_runs.values():
            assert (
                db.execute("SELECT state FROM model_calls WHERE run_id=?", (rid,)).fetchone()[0]
                == "settled"
            )


def test_visual_primary_abort_keeps_usage_and_never_publishes(
    config, entry, policy, monkeypatch, tmp_path, media
):
    from test_intake_abort_commands import command

    from fiber_bundle import feishu_receiver

    batch, _ = install_v3(
        config, entry, policy, monkeypatch, tmp_path, media, subtitles=False, empty_asr=True
    )
    provider = model_gateway.complete

    def reply(*args):
        _, link = link_for(config, entry, batch)
        command(config, entry, "abort " + link["video_processing"]["job_id"])
        return provider(*args)

    monkeypatch.setattr(model_gateway, "complete", reply)
    assert intake_extraction.advance(config, entry[0]["id"])
    feishu_receiver.advance(config, entry[1])
    _, link = link_for(config, entry, batch)
    assert link["state"] == "cancelled" and "publication" not in link
    with database.connect(config.state_root) as db:
        assert tuple(
            db.execute("SELECT state,input_tokens,output_tokens FROM model_calls").fetchone()
        ) == ("settled", 20, 20)


def test_invalid_skip_response_is_retained_without_automatic_paid_retry(
    config, entry, policy, monkeypatch, tmp_path, media
):
    batch, _ = install_v3(
        config, entry, policy, monkeypatch, tmp_path, media, subtitles=False, empty_asr=True
    )
    provider = model_gateway.complete
    calls = []

    def invalid(*args):
        result = provider(*args)
        result["choices"][0]["message"]["content"] = json.dumps(
            {
                "analysis_status": "not_needed",
                "skip_reason_code": "title_card",
                "review_note": "Opening frame",
                "illustrations": [],
            }
        )
        calls.append(1)
        return result

    monkeypatch.setattr(model_gateway, "complete", invalid)
    assert intake_extraction.advance(config, entry[0]["id"])
    record, link = link_for(config, entry, batch)
    assert link["reason"] == "video_skill_visual_response_invalid"
    frozen = link["video_processing"]
    problem = frozen["visual_response_error"]
    assert problem["field"] == "skip_reason_code"
    response = config.state_root / "video-skill-jobs" / frozen["job_id"] / problem["response_path"]
    assert json.loads(response.read_text())["run_id"] == problem["run_id"]
    assert json.loads(response.read_text())["body"]["choices"][0]["message"]["content"]
    assert not intake_extraction.advance(config, entry[0]["id"])
    assert len(calls) == 1 and "publication" not in link
    with pytest.raises(InvalidInput, match="new_attempt_confirmation_required"):
        intake_extraction.retry(config, entry[0]["id"], batch, 0, record["revision"])
    monkeypatch.setattr(model_gateway, "complete", provider)
    intake_extraction.retry(
        config, entry[0]["id"], batch, 0, record["revision"], approve_new_model_attempt=True
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    _, link = link_for(config, entry, batch)
    assert link["state"] == "review_required"
    assert "visual_response_error" not in link["video_processing"]
    assert response.exists()


def test_cancelled_automatic_video_job_gets_successor_and_saves_visual_note(
    config, entry, policy, monkeypatch, tmp_path, media
):
    from fiber_bundle import automatic_jobs

    batch, _ = install_v3(
        config,
        entry,
        policy,
        monkeypatch,
        tmp_path,
        media,
        subtitles=False,
        empty_asr=True,
        direct=True,
    )
    with database.connect(config.state_root) as db:
        old = automatic_jobs.get(
            db,
            db.execute(
                "SELECT id FROM automatic_jobs WHERE json_extract(value,'$.intake.batch')=?",
                (batch,),
            ).fetchone()[0],
        )
        automatic_jobs.settle(db, old, "cancelled")
    record, _ = link_for(config, entry, batch)
    intake_extraction.retry(config, entry[0]["id"], batch, 0, record["revision"])
    assert intake_extraction.advance(config, entry[0]["id"])
    for _ in range(6):
        automatic_ingestion.tick(config)
    _, link = link_for(config, entry, batch)
    assert link["publication"]["state"] == "published", link
    note = config.vault_path("test vault/temp_vault1") / link["publication"]["entry_note_path"]
    assert DISCLOSURE in note.read_text()
    with database.connect(config.state_root) as db:
        assert automatic_jobs.get(db, old["id"])["state"] == "cancelled"
        leaf = db.execute(
            "SELECT state FROM automatic_jobs WHERE json_extract(value,'$.supersedes_job')=?",
            (old["id"],),
        ).fetchone()
        assert leaf[0] == "complete"
