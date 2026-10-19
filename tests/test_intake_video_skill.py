"""Real V3 frames/crops/evidence/audits with synthetic transport and model responses."""

import json
import shutil
import subprocess

import pytest
from test_channel_intake import enable, receive, records
from test_intake_separated_flow import capture
from test_video_skill import configured

from fiber_bundle import (
    automatic_ingestion,
    database,
    execution_ledger,
    model_gateway,
    pi_processing,
    search,
    video_skill,
)
from fiber_bundle.channels import intake_extraction, intake_processing
from fiber_bundle.config import InvalidInput
from fiber_bundle.inputs import canonical
from fiber_bundle.query_policy_store import write_private

pytest_plugins = ["test_channel_intake", "test_execution"]
URL = "https://www.bilibili.com/video/BV1XC4y197GR/"


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("v3-synthetic")
    path = root / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "4",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-threads",
            "1",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    return path


def install_v3(
    config,
    entry,
    policy,
    monkeypatch,
    tmp_path,
    media,
    subtitles=True,
    direct=True,
    empty_asr=False,
):
    # Reuse the real policy/layout preparation without keeping its sample article.
    capture(config, entry, policy, monkeypatch, "pi_skill", direct)
    from fiber_bundle import model_budgets

    with database.connect(config.state_root) as db:
        model_budgets.save(
            db,
            model_budgets.Save(
                secret_file=policy.secret_file,
                expected_revision=0,
                settings=model_budgets.Policy(label="Synthetic", limit=1000000),
            ),
        )
    for _ in range(5):
        automatic_ingestion.tick(config)
    configured(config, tmp_path)
    enable(config, entry, policy_version=2, fetch_subtitles=True, fetch_articles=True)
    batch = receive(config, entry, text=URL, identity="video-message")
    intake_processing.advance(config, entry[0]["id"])
    original = video_skill.run
    steps = []

    def run(config, binding, job, script, args, guard, **kwargs):
        guard()
        steps.append(script + (":" + args[-1] if script == "acquire_public" else ""))
        if script == "fiber_public_probe":
            write_private(
                job / "metadata/source_metadata.json",
                canonical(
                    {
                        "id": "BV1XC4y197GR",
                        "title": "V3 Synthetic Video",
                        "duration": 4,
                        "webpage_url": URL,
                        "availability": "public",
                    }
                ).encode(),
            )
            access = json.loads((job / "metadata/access.json").read_bytes())
            access.update(
                status="public", access_state="public_verified", verified_by="synthetic_transport"
            )
            write_private(job / "metadata/access.json", canonical(access).encode())
            return
        if script == "acquire_public":
            if args[-1] == "media":
                target = job / "source/video.mp4"
                shutil.copy2(media, target)
                # Real V3 registration and ffprobe coverage, without network acquisition.
                import sys

                sys.path.insert(0, str(video_skill.verify(config, binding) / "scripts"))
                from acquire_public import record_source_coverage
                from common import register_managed_file

                register_managed_file(job, target, kind="source_video", origin="synthetic")
                record_source_coverage(job, target)
                return
            if args[-1] == "subtitles":
                write_private(
                    job / "transcript/subtitle_selection.json",
                    canonical(
                        {
                            "status": "downloaded" if subtitles else "absent",
                            "files": ["transcript/input.srt"] if subtitles else [],
                        }
                    ).encode(),
                )
                if subtitles:
                    write_private(
                        job / "transcript/input.srt",
                        b"1\n00:00:00,000 --> 00:00:04,000\n"
                        b"Synthetic source explains the diagram.\n",
                    )
                return
        if script == "run_asr":
            if empty_asr:
                import wave

                from fiber_bundle import video_visual

                audio = job / "transcript/asr_audio.wav"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-i",
                        str(job / "source/video.mp4"),
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        str(audio),
                    ],
                    check=True,
                    timeout=30,
                )
                with wave.open(str(audio)) as stream:
                    duration = stream.getnframes() / stream.getframerate()
                write_private(job / "transcript/asr_prompt.txt", b"synthetic ASR fixture")
                root = video_skill.verify(config, binding)
                write_private(
                    job / "transcript/asr_diagnostic.json",
                    canonical(
                        {
                            "schema": "fiber-v3-asr-diagnostic/v1",
                            "source_media_sha256": video_visual.checksum(job / "source/video.mp4"),
                            "audio_sha256": video_visual.checksum(audio),
                            "context_sha256": video_visual.checksum(
                                job / "transcript/asr_prompt.txt"
                            ),
                            "backend_sha256": video_visual.checksum(
                                root / "scripts/qwen3_asr_backend.py"
                            ),
                            "transcript_segments": 0,
                            "transcript_characters": 0,
                            "chunks": [
                                {
                                    "chunk_index": 1,
                                    "start": 0,
                                    "end": round(duration, 3),
                                    "status": "empty",
                                }
                            ],
                        }
                    ).encode(),
                )
                raise InvalidInput("video_skill_asr_empty")
            # Route test supplies timestamped ASR evidence. Actual local model
            # qualification is recorded separately, not claimed by this fixture.
            raw = job / "transcript/asr_raw.srt"
            write_private(
                raw,
                b"1\n00:00:00,000 --> 00:00:04,000\nSynthetic ASR source explains the diagram.\n",
            )
            original(
                config,
                binding,
                job,
                "normalize_transcript",
                [str(job), "--input", str(raw), "--kind", "asr"],
                guard,
            )
            write_private(
                job / "transcript/asr_coverage.json",
                canonical(
                    {
                        "complete": True,
                        "failed_chunk_count": 0,
                        "gap_ranges": [],
                        "coverage_ratio": 1.0,
                        "total_chunks": 1,
                        "duration_seconds": 4,
                    }
                ).encode(),
            )
            return
        return original(config, binding, job, script, args, guard, **kwargs)

    monkeypatch.setattr(video_skill, "run", run)
    monkeypatch.setattr(model_gateway, "prepare", lambda policy, value: value)

    def complete(policy, rid, payload):
        assert "data:image/jpeg;base64," in canonical(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": canonical(
                            {
                                "analysis_status": "analyzed",
                                "visual_type": "diagram",
                                "summary": "Synthetic diagram",
                                "ocr_text": ["Synthetic diagram method"],
                                "claims": [],
                                "action": None,
                                "confidence": 0.9,
                                "redactions": [],
                                "illustrations": [
                                    {
                                        "region_id": "R01",
                                        "kind": "diagram",
                                        "bbox_norm": [0.1, 0.1, 0.8, 0.8],
                                        "caption": "Synthetic diagram",
                                        "recommended": True,
                                    }
                                ],
                            }
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 20},
        }

    monkeypatch.setattr(model_gateway, "complete", complete)

    def execute(config, policy, rid, prompt):
        with database.connect(config.state_root) as db:
            execution_ledger.start(config, db, rid, policy)
            binding = pi_processing.get(db, rid)
            pi_processing.materialize(config, binding)
            data = json.loads(binding["input_json"])
            assert data["video_skill"]["pi_sha256"] == binding["skill_sha256"]
            write_private(
                config.state_root / "tasks" / rid / "draft.json",
                canonical(
                    {
                        "schema": "fiber-processing-draft/v1",
                        "kind": "video",
                        "source_sha256": binding["source_sha256"],
                        "summary": "Synthetic learning note",
                        "evidence_ids": [data["evidence"][0]["id"]],
                        "note_markdown": "### Diagram method\n\n"
                        "Synthetic source explains the diagram.",
                        "gaps": [],
                    }
                ).encode(),
            )
            execution_ledger.transition(db, rid, "succeeded")

    monkeypatch.setattr("fiber_bundle.execution.execute", execute)
    return batch, steps


@pytest.mark.parametrize("subtitles", [True, False])
@pytest.mark.parametrize("direct", [True, False])
def test_video_success_obeys_review_mode_and_uses_v3_artifacts(
    config, entry, policy, monkeypatch, tmp_path, media, subtitles, direct
):
    batch, steps = install_v3(
        config, entry, policy, monkeypatch, tmp_path, media, subtitles, direct
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    link = json.loads(next(r for r in records(config, entry) if r["batch_id"] == batch)["value"])[
        "links"
    ][0]
    assert link["state"] == "review_required", link
    assert ("run_asr" in steps) is (not subtitles)
    assert link["metadata"]["audit_passed"]
    assert link["metadata"]["image_count"] >= 1
    assert "analysis.md" not in link["files"]
    for _ in range(6):
        automatic_ingestion.tick(config)
    link = json.loads(next(r for r in records(config, entry) if r["batch_id"] == batch)["value"])[
        "links"
    ][0]
    assert (link.get("publication", {}).get("state") == "published") is direct
    if direct:
        path = config.vault_path("test vault/temp_vault1") / link["publication"]["entry_note_path"]
        assert "Diagram method" in path.read_text()
        assert "../illustrations/" not in path.read_text()
        with database.connect(config.state_root) as db:
            hits = search.vault_search(
                config, db, entry[1].client_id, "Diagram method", [entry[1].vault_id]
            )["results"]
            assert hits and hits[0]["source_kind"] == "agent_analysis"
            assert not hits[0]["independent_evidence"]
            assert "视频提取" in hits[0]["source_label"]
    before = list(steps)
    assert not intake_extraction.advance(config, entry[0]["id"])
    assert steps == before


@pytest.mark.parametrize("short_link", [False, True])
def test_video_timeout_requires_approved_new_call_and_reuses_media_and_transcript(
    config, entry, policy, monkeypatch, tmp_path, media, short_link
):
    import sys

    from test_article_fetch import Reply, install

    from fiber_bundle.model_configuration.errors import ModelError

    if short_link:
        monkeypatch.setattr(sys.modules[__name__], "URL", "https://b23.tv/synthetic")

    batch, steps = install_v3(config, entry, policy, monkeypatch, tmp_path, media)
    calls = []
    if short_link:
        calls, _ = install(
            monkeypatch,
            [
                Reply(
                    302,
                    b"",
                    [
                        (
                            "Location",
                            "https://www.bilibili.com/video/BV1XC4y197GR/?share_source=copy",
                        )
                    ],
                )
            ],
        )
    provider = model_gateway.complete
    monkeypatch.setattr(
        model_gateway, "complete", lambda *a: (_ for _ in ()).throw(ModelError("model_timeout"))
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    saved = next(r for r in records(config, entry) if r["batch_id"] == batch)
    link = json.loads(saved["value"])["links"][0]
    assert link["state"] == "needs_review" and link["reason"] == "model_timeout"
    assert "publication" not in link
    old_runs = dict(link["video_processing"]["vision_runs"])
    with pytest.raises(InvalidInput, match="new_attempt_confirmation_required"):
        intake_extraction.retry(config, entry[0]["id"], batch, 0, saved["revision"])
    monkeypatch.setattr(model_gateway, "complete", provider)
    intake_extraction.retry(
        config, entry[0]["id"], batch, 0, saved["revision"], approve_new_model_attempt=True
    )
    before = list(steps)
    assert intake_extraction.advance(config, entry[0]["id"])
    link = json.loads(next(r for r in records(config, entry) if r["batch_id"] == batch)["value"])[
        "links"
    ][0]
    assert link["state"] == "review_required", link
    repeated = steps[len(before) :]
    if short_link:
        assert len(calls) == 1
        assert (
            link["video_processing"]["source_resolution"]["final_url"]
            == "https://www.bilibili.com/video/BV1XC4y197GR"
        )
    assert not any(
        s.startswith("acquire_public") or s in {"run_asr", "select_keyframes"} for s in repeated
    )
    with database.connect(config.state_root) as db:
        for rid in old_runs.values():
            assert execution_ledger.get_run(db, rid)["state"] == "failed"
            assert (
                db.execute("SELECT state FROM model_calls WHERE run_id=?", (rid,)).fetchone()[0]
                == "uncertain"
            )
