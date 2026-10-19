"""Explicit existing-evidence recovery saves under normal rules without any new inference."""

import json

import pytest
from pydantic import ValidationError
from test_channel_intake import records
from test_intake_video_skill import install_v3
from test_intake_video_skill import media as media

from fiber_bundle import automatic_ingestion, database, execution_ledger, model_gateway
from fiber_bundle.channels import intake_contract, intake_extraction
from fiber_bundle.config import InvalidInput
from fiber_bundle.video_evidence_notes import segment_note
from fiber_bundle.video_skill_notes import export
from fiber_bundle.video_visual import DISCLOSURE

pytest_plugins = ["test_channel_intake", "test_execution"]


def link_for(config, entry, batch):
    row = next(r for r in records(config, entry) if r["batch_id"] == batch)
    return row, json.loads(row["value"])["links"][0]


def prepared(config, entry, policy, monkeypatch, tmp_path, media, direct=True):
    batch, steps = install_v3(
        config,
        entry,
        policy,
        monkeypatch,
        tmp_path,
        media,
        subtitles=False,
        empty_asr=True,
        direct=direct,
    )

    def failed(c, p, rid, prompt):
        with database.connect(c.state_root) as db:
            execution_ledger.start(c, db, rid, p)
            call = execution_ledger.reserve(c, db, rid, p)
            execution_ledger.settle(db, call, p, {"prompt_tokens": 10, "completion_tokens": 20})
            execution_ledger.transition(db, rid, "failed", "processing_model_incomplete")

    monkeypatch.setattr("fiber_bundle.execution.execute", failed)
    assert intake_extraction.advance(config, entry[0]["id"])
    row, link = link_for(config, entry, batch)
    assert link["reason"] == "video_skill_note_requires_recovery"
    automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        calls = [tuple(r) for r in db.execute("SELECT * FROM model_calls")]
        reservations = [tuple(r) for r in db.execute("SELECT * FROM model_budget_reservations")]
    monkeypatch.setattr(model_gateway, "complete", lambda *a: pytest.fail("new inference"))
    monkeypatch.setattr("fiber_bundle.execution.execute", lambda *a: pytest.fail("new Pi run"))
    return batch, steps, row, link, calls, reservations


@pytest.mark.parametrize("direct", [False, True])
def test_paid_failure_can_use_cached_evidence_under_original_review_and_save_rules(
    config, entry, policy, monkeypatch, tmp_path, media, direct
):
    batch, steps, row, old, calls, reservations = prepared(
        config, entry, policy, monkeypatch, tmp_path, media, direct
    )
    with pytest.raises(InvalidInput, match="new_attempt_confirmation_required"):
        intake_extraction.retry(config, entry[0]["id"], batch, 0, row["revision"])
    intake_extraction.retry(
        config, entry[0]["id"], batch, 0, row["revision"], video_note_mode="visual_evidence"
    )
    start = len(steps)
    assert intake_extraction.advance(config, entry[0]["id"])
    _, link = link_for(config, entry, batch)
    assert link["state"] == "review_required", link
    frozen = link["video_processing"]
    assert frozen["note_runs"] == old["video_processing"]["note_runs"]
    assert frozen["vision_runs"] == old["video_processing"]["vision_runs"]
    assert all(s.startswith("fiber_visual") for s in steps[start:])
    job = config.state_root / "video-skill-jobs" / frozen["job_id"]
    exported = export(job, frozen)
    provenance = json.loads(exported["video-provenance.json"])
    assert provenance["note_authoring"] == "visual_evidence" and provenance["note_runs"] == {}
    assert provenance["unused_prior_note_runs"] == frozen["note_runs"]
    assert DISCLOSURE in exported["note.md"].decode()
    for _ in range(6):
        automatic_ingestion.tick(config)
    _, link = link_for(config, entry, batch)
    assert ((link.get("publication") or {}).get("state") == "published") is direct
    if direct:
        path = config.vault_path("test vault/temp_vault1") / link["publication"]["entry_note_path"]
        assert "画面文字" in path.read_text() and "未调用 Pi" in path.read_text()
    with database.connect(config.state_root) as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM model_calls")] == calls
        assert [
            tuple(r) for r in db.execute("SELECT * FROM model_budget_reservations")
        ] == reservations


@pytest.mark.parametrize(
    "damage", ["missing_frame", "changed_frame", "changed_visual", "incomplete_stage"]
)
def test_missing_or_changed_evidence_fails_without_model_fallback(
    config, entry, policy, monkeypatch, tmp_path, media, damage
):
    batch, _, row, old, calls, _ = prepared(config, entry, policy, monkeypatch, tmp_path, media)
    frozen = old["video_processing"]
    job = config.state_root / "video-skill-jobs" / frozen["job_id"]
    frame = json.loads((job / "frames/frame_manifest.json").read_text())["frames"][0]
    if damage == "missing_frame":
        (job / frame["selected_path"]).unlink()
    elif damage == "changed_frame":
        with (job / frame["selected_path"]).open("ab") as f:
            f.write(b"changed")
    elif damage == "changed_visual":
        path = job / "visual" / (frame["frame_id"] + ".json")
        value = json.loads(path.read_text())
        value["summary"] = "unbound change"
        path.write_text(json.dumps(value))
    else:
        frozen["completed"].remove("timeline")
        intake_extraction.update(config, entry[0]["id"], batch, 0, {"video_processing": frozen})
    if damage == "incomplete_stage":
        with pytest.raises(InvalidInput, match="visual_audit_failed"):
            intake_extraction.retry(
                config, entry[0]["id"], batch, 0, row["revision"], video_note_mode="visual_evidence"
            )
    else:
        intake_extraction.retry(
            config, entry[0]["id"], batch, 0, row["revision"], video_note_mode="visual_evidence"
        )
        assert intake_extraction.advance(config, entry[0]["id"])
        _, link = link_for(config, entry, batch)
        assert link["state"] == "needs_review" and not link.get("publication")
    with database.connect(config.state_root) as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM model_calls")] == calls
        assert (
            db.execute("SELECT count(*) FROM publication_locations WHERE kind='intake'").fetchone()[
                0
            ]
            == 1
        )  # The setup's unrelated article only.


def test_source_markup_is_displayed_as_text_without_injected_images_or_html():
    note = segment_note(
        {
            "visual_evidence": [
                {
                    "timestamp": 3.0,
                    "analysis": {
                        "summary": '<img src="x">',
                        "ocr_text": ["![x](http://invalid)", "<script>x</script>"],
                        "claims": ["# title"],
                    },
                }
            ]
        }
    )
    assert "![" not in note and "<img" not in note and "<script" not in note
    assert "&lt;script&gt;" in note


@pytest.mark.parametrize("mode", [True, "pi", "other", 1])
def test_recovery_mode_is_an_explicit_validated_option(mode):
    with pytest.raises(ValidationError):
        intake_contract.Retry(expected_revision=1, video_note_mode=mode)
