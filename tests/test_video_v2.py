"""Long media exercises real ffmpeg plus persisted progression; provider use stays synthetic."""

import json
import shutil
import subprocess
from uuid import uuid4

import pytest
from test_publication_layout import activate
from test_source_imports import request as import_request
from test_source_imports import upload

from fiber_bundle import (
    automatic_policy,
    database,
    media_subtitles,
    video_capture,
    video_contract,
    video_runner,
    video_tasks,
    video_tools,
)
from fiber_bundle.config import InvalidInput, make_directory
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.query_policy_store import write_private

pytest_plugins = ["test_source_imports", "test_execution"]


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed for actual synthetic long-video check")
    root = tmp_path_factory.mktemp("long-video")
    path = root / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "70",
            "-c:v",
            "mpeg4",
            "-q:v",
            "8",
            "-c:a",
            "aac",
            "-threads",
            "1",
            str(path),
        ],
        check=True,
        timeout=60,
    )
    return path.read_bytes()


def setup(config, owner, clip, check=True, direct=False, subtitle=True):
    cid, _, ids = owner
    vid = ids["personal"]
    from fiber_bundle.vault_save_contract import settings_wire, unified_settings

    layout = settings_wire(unified_settings())
    layout.pop("layout_mode")
    activate(config, vid, **layout)
    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            vid,
            0,
            automatic_policy.Policy(
                policy_version=2,
                enabled=True,
                client_id=cid,
                completeness_check=check,
                direct_to_inbox=direct,
                allowed_kinds=["media"],
            ),
        )
        imported = upload(config, db, import_request(cid, vid, clip, filename="long.mp4"), clip)
        video_tasks.configure(
            config,
            video_contract.Settings(image="sha256:" + "a" * 64),
            video_tasks.settings(config)["sha256"],
        )
        task = video_tasks.create(
            config,
            db,
            video_contract.Create(
                client_id=cid,
                import_id=imported["id"],
                duration_ms=70000,
                frames=True,
                subtitle="1\n00:00:00,000 --> 00:01:10,000\nSynthetic long video evidence\n"
                if subtitle
                else "",
            ),
        )
    return cid, vid, task


def actual_tool(config, source, spec, resources, guard):
    guard()
    root = config.state_root / "video-test-capture" / str(uuid4())
    make_directory(root / "input")
    make_directory(root / "output")
    make_directory(root / "temporary")
    source.copy_new(root / "input/source")
    write_private(root / "input/request.json", canonical(spec).encode())
    video_capture.capture(root, temporary=root / "temporary")
    value = json.loads((root / "output/result.json").read_bytes())
    video_contract.verify(value, spec)
    guard()
    return {
        "attempt_id": root.name,
        "result_sha256": digest(canonical(value).encode()),
        "result": value,
    }


@pytest.mark.parametrize(
    "check,direct", [(True, False), (True, True), (False, False), (False, True)]
)
def test_actual_long_video_frames_and_four_switches(
    config, owner, clip, monkeypatch, check, direct
):
    cid, vid, task = setup(config, owner, clip, check, direct)
    monkeypatch.setattr(video_tools, "execute", actual_tool)
    result = video_runner.run(config, cid, task["id"])
    assert result["state"] == ("published" if direct else "needs_confirmation")
    assert result["completeness_status"] == ("passed" if check else "not_checked")
    assert len(result["segments"]) == 2 and all(
        s["state"] == "complete" for s in result["segments"]
    )
    assert sum(len(s["result"]["frames"]) for s in result["segments"]) == 7
    assert result["known_gaps"] == [] and result["knowledge_verified"] is False
    with database.connect(config.state_root) as db:
        if not direct:
            result = video_tasks.action(config, db, cid, task["id"], "publish")
            assert result["state"] == "published"
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
        location = db.execute(
            "SELECT entry_note_path FROM publication_locations WHERE publication_id=?",
            (task["id"],),
        ).fetchone()
        from fiber_bundle.vault_directories import vault_root

        root = vault_root(config, db, vid)[0]
        note = (root / location[0]).read_text()
        assert "[60000–70000 ms]" in note and "Synthetic long video evidence" in note


def test_cancel_after_first_segment_resumes_without_repeating_completed_tool(
    config, owner, clip, monkeypatch
):
    cid, _, task = setup(config, owner, clip)
    calls = []

    def tool(config, source, spec, resources, guard):
        calls.append(spec["start_ms"])
        if spec["start_ms"] == 60000 and len(calls) == 2:
            with database.connect(config.state_root) as db:
                video_tasks.action(config, db, cid, task["id"], "cancel")
            guard()
        return actual_tool(config, source, spec, resources, guard)

    monkeypatch.setattr(video_tools, "execute", tool)
    with pytest.raises(InvalidInput, match="stopped"):
        video_runner.run(config, cid, task["id"])
    with database.connect(config.state_root) as db:
        assert video_tasks.get(config, db, cid, task["id"])["state"] == "cancelled"
        video_tasks.action(config, db, cid, task["id"], "resume")
    assert video_runner.run(config, cid, task["id"])["state"] == "needs_confirmation"
    assert calls == [0, 60000, 60000]


def test_no_subtitle_without_local_asr_has_actionable_failure_and_original(config, owner, clip):
    with pytest.raises(InvalidInput, match="transcription_capability_missing"):
        setup(config, owner, clip, subtitle=False)
    with database.connect(config.state_root) as db:
        assert (
            db.execute("SELECT count(*) FROM source_imports WHERE state='archived'").fetchone()[0]
            == 1
        )
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


def test_versioned_subtitle_limits_and_timeline_gaps():
    raw = b"1\n00:00:00,000 --> 00:01:10,000\nlong\n"
    from fiber_bundle.channels.intake_video import duration

    with pytest.raises(InvalidInput):
        duration(70)
    assert duration(70, long_video=True) == 70000
    cues = media_subtitles.parse(raw, "srt", 70000, long_video=True)
    assert media_subtitles.gaps(cues, 70000) == []
    with pytest.raises(InvalidInput):
        video_contract.segments(video_contract.MAX_DURATION_MS + 1)
    assert len(video_contract.segments(video_contract.MAX_DURATION_MS)) == 120


def test_local_asr_route_reuses_transcriber_and_reports_missing_ranges(
    config, owner, clip, monkeypatch
):
    from test_media_processing import RESOURCE

    from fiber_bundle import media_asr_runtime
    from fiber_bundle.vault_save_contract import settings_wire, unified_settings

    cid, _, ids = owner
    vid = ids["personal"]
    layout = settings_wire(unified_settings())
    layout.pop("layout_mode")
    activate(config, vid, **layout)
    monkeypatch.setattr(media_asr_runtime, "require", lambda p: None)
    resources = {**RESOURCE, "asr_enabled": True, "qualified": True}
    with database.connect(config.state_root) as db:
        imported = upload(config, db, import_request(cid, vid, clip, filename="asr.mp4"), clip)
        video_tasks.configure(
            config,
            video_contract.Settings(image=resources["image"], asr_resources=resources),
            video_tasks.settings(config)["sha256"],
        )
        task = video_tasks.create(
            config,
            db,
            video_contract.Create(
                client_id=cid,
                import_id=imported["id"],
                duration_ms=70000,
                glossary={
                    "schema": "fiber-media-glossary/v1",
                    "topic": "",
                    "terms": [],
                    "unresolved": [],
                },
                glossary_confirmed=True,
            ),
        )

    def transcribe(source, spec):
        duration = spec["duration_ms"]
        return [
            {
                "start_ms": 0,
                "end_ms": duration,
                "text": "Synthetic transcriber result",
                "state": "complete",
                "language": "English",
            }
        ]

    monkeypatch.setattr(video_capture, "asr", transcribe)
    monkeypatch.setattr(video_tools, "execute", actual_tool)
    result = video_runner.run(config, cid, task["id"])
    assert all(s["result"]["transcript_route"] == "local_qwen3_asr" for s in result["segments"])
    assert result["state"] == "needs_confirmation" and result["known_gaps"] == []


def test_public_link_archives_original_and_uses_same_long_video_pipeline(
    config, owner, clip, monkeypatch
):
    from fiber_bundle import video_links

    cid, _, ids = owner
    vid = ids["personal"]
    settings = video_contract.Settings(image="sha256:" + "a" * 64, link_image="sha256:" + "b" * 64)
    video_tasks.configure(config, settings, video_tasks.settings(config)["sha256"])
    with database.connect(config.state_root) as db:
        task = video_links.create(
            config,
            db,
            video_links.Create(
                client_id=cid,
                vault_id=vid,
                url="https://www.youtube.com/watch?v=abcdefghijk",
                duration_ms=70000,
                subtitle="1\n00:00:00,000 --> 00:01:10,000\nLinked evidence\n",
            ),
        )
    calls = []

    def acquire(root, attempt, image, entry, timeout, guard, **kwargs):
        calls.append(entry)
        assert kwargs["network"] == "bridge"
        guard()
        spec = json.loads((root / "input/request.json").read_bytes())
        write_private(root / "output/source", clip)
        write_private(
            root / "output/result.json",
            canonical(
                {
                    "schema": "fiber-media-link-capture/v3",
                    **spec,
                    "converted_duration_ms": 70000,
                    "source_sha256": digest(clip),
                    "source_bytes": len(clip),
                    "media_type": "video/mp4",
                    "access_state": "public_acquired",
                    "source_coverage_verified": False,
                    "cookies_exported": False,
                }
            ).encode(),
        )

    monkeypatch.setattr(video_tools, "container", acquire)
    monkeypatch.setattr(video_tools, "execute", actual_tool)
    video_links.run(config, cid, task["id"])
    with database.connect(config.state_root) as db:
        saved = video_links.get(config, db, cid, task["id"])
        assert saved["state"] == "complete"
        video = video_tasks.project(video_tasks.get(config, db, cid, saved["value"]["video_id"]))
        assert video["state"] == "needs_confirmation" and len(video["segments"]) == 2
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    assert calls == ["video_link_entry"]


def test_real_duration_mismatch_is_not_hidden_by_declared_timeline(
    config, owner, clip, monkeypatch
):
    cid, _, task = setup(config, owner, clip)
    with database.connect(config.state_root) as db:
        row = video_tasks.get(config, db, cid, task["id"])
        frozen = json.loads(row["policy_json"])
        frozen["duration_ms"] = 69000
        db.execute(
            "UPDATE video_tasks SET policy_json=? WHERE id=?", (canonical(frozen), task["id"])
        )
    monkeypatch.setattr(video_tools, "execute", actual_tool)
    with pytest.raises(InvalidInput, match="source_duration_mismatch"):
        video_runner.run(config, cid, task["id"])
    with database.connect(config.state_root) as db:
        saved = video_tasks.project(video_tasks.get(config, db, cid, task["id"]))
        assert saved["error_code"] == "video_source_duration_mismatch"
        assert saved["state"] == "failed"


def test_pi_video_charges_two_segments_and_preserves_timestamps(
    config, owner, clip, policy, monkeypatch
):
    from fiber_bundle import execution_ledger, pi_processing

    cid, vid, task = setup(config, owner, clip)
    monkeypatch.setattr(automatic_policy, "processing_route", lambda *args: policy)
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

    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            vid,
            1,
            automatic_policy.Policy(
                policy_version=2,
                enabled=True,
                client_id=cid,
                processing_mode="pi_skill",
                model_configuration_id="opencodego-primary",
                allow_model_source_content=True,
                allowed_kinds=["media"],
            ),
        )
        frozen = json.loads(
            db.execute("SELECT policy_json FROM video_tasks WHERE id=?", (task["id"],)).fetchone()[
                0
            ]
        )
        frozen["vault_policy"] = automatic_policy.get(db, vid)
        frozen["model_configuration_sha256"] = policy.digest
        db.execute(
            "UPDATE video_tasks SET policy_json=? WHERE id=?", (canonical(frozen), task["id"])
        )

    def execute(config, model, rid, prompt):
        with database.connect(config.state_root) as db:
            execution_ledger.start(config, db, rid, model)
            call = execution_ledger.reserve(config, db, rid, model)
            execution_ledger.settle(db, call, model, {"prompt_tokens": 10, "completion_tokens": 5})
            binding = pi_processing.get(db, rid)
            pi_processing.materialize(config, binding)
            data = json.loads(binding["input_json"])
            assert data["kind"] == "video"
            write_private(
                config.state_root / "tasks" / rid / "draft.json",
                canonical(
                    {
                        "schema": "fiber-processing-draft/v1",
                        "kind": "video",
                        "source_sha256": binding["source_sha256"],
                        "summary": "Timestamp-grounded video analysis",
                        "evidence_ids": [data["evidence"][0]["id"]],
                        "gaps": [],
                    }
                ).encode(),
            )
            execution_ledger.transition(db, rid, "succeeded")

    monkeypatch.setattr(video_tools, "execute", actual_tool)
    monkeypatch.setattr("fiber_bundle.execution.execute", execute)
    result = video_runner.run(config, cid, task["id"])
    assert result["state"] == "needs_confirmation"
    assert all(s["analysis"]["knowledge_verified"] is False for s in result["segments"])
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 2
        assert (
            db.execute("SELECT sum(input_tokens+output_tokens) FROM model_calls").fetchone()[0]
            == 30
        )
    assert "analysis.md" in result["artifact_files"]
