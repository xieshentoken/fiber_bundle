from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def run_script(name: str, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *map(str, arguments)],
        check=check,
        capture_output=True,
        text=True,
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_script_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PipelineTests(unittest.TestCase):
    def make_job(self, root: Path, evidence_mode: str = "full") -> Path:
        job = root / "job"
        arguments = [
            "https://www.bilibili.com/video/BV0000000000/",
            "--out",
            str(job),
            "--topic",
            "核聚变 托卡马克",
        ]
        if evidence_mode != "full":
            arguments.extend(["--evidence-mode", evidence_mode])
        run_script("init_job.py", *arguments)
        run_script("set_access_state.py", str(job), "--status", "public")
        return job

    def make_youtube_job(self, root: Path, evidence_mode: str = "full") -> Path:
        job = root / "youtube-job"
        arguments = [
            "https://www.youtube.com/watch?v=abcdefghijk&list=PL-not-used",
            "--out",
            str(job),
            "--topic",
            "multilingual tutorial",
        ]
        if evidence_mode != "full":
            arguments.extend(["--evidence-mode", evidence_mode])
        run_script("init_job.py", *arguments)
        run_script("set_access_state.py", str(job), "--status", "public")
        return job

    def test_youtube_source_normalization_and_job_schema(self) -> None:
        import common

        expected = ("youtube", "abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(common.validate_video_source("abcdefghijk"), expected)
        self.assertEqual(common.validate_video_source("https://youtu.be/abcdefghijk?t=3"), expected)
        self.assertEqual(common.validate_video_source("https://www.youtube.com/shorts/abcdefghijk"), expected)
        with self.assertRaisesRegex(ValueError, "valid YouTube video id"):
            common.validate_video_source("https://www.youtube.com/@example")

        with tempfile.TemporaryDirectory() as directory:
            job = self.make_youtube_job(Path(directory))
            config = read_json(job / "metadata" / "job.json")
            self.assertEqual(config["schema_version"], "3.0")
            self.assertEqual(config["source"]["platform"], "youtube")
            self.assertEqual(config["source"]["id"], "abcdefghijk")
            self.assertEqual(
                config["source"]["url"],
                "https://www.youtube.com/watch?v=abcdefghijk",
            )
            self.assertNotIn("bvid", config["source"])
            self.assertEqual(config["policies"]["live_chat"], "forbidden")
            gate = json.loads(run_script("job_gate.py", str(job), "--json").stdout)
            self.assertEqual(gate["platform"], "youtube")
            self.assertEqual(gate["source_id"], "abcdefghijk")
            self.assertEqual(gate["resume_phase"], "terminology")

    def add_and_confirm_best(self, job: Path) -> None:
        run_script(
            "manage_glossary.py",
            "add",
            str(job),
            "--canonical",
            "BEST",
            "--full-name",
            "Burning plasma Experimental Superconducting Tokamak",
            "--context",
            "核聚变",
            "--context",
            "托卡马克",
            "--alias",
            "best",
            "--alias",
            "beast",
            "--safe-alias",
            "贝斯特",
        )
        run_script("manage_glossary.py", "confirm", str(job), "--by", "user")

    def test_job_and_glossary_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            self.add_and_confirm_best(job)
            report = run_script("audit_job.py", str(job), "--stage", "ready-for-asr")
            self.assertIn("ready-for-asr: PASS", report.stdout)
            config = read_json(job / "metadata" / "job.json")
            self.assertEqual(config["policies"]["danmaku"], "forbidden")
            asr_context = read_json(job / "terms" / "asr_context.json")
            self.assertIn("BEST", asr_context["hotwords"])
            self.assertNotIn("best", asr_context["hotwords"])

    def test_contextual_term_normalization_preserves_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            self.add_and_confirm_best(job)
            srt = Path(directory) / "input.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:03,000\n中国核聚变托卡马克项目\n\n"
                "2\n00:00:03,000 --> 00:00:06,000\n它叫做 best\n\n"
                "3\n00:00:20,000 --> 00:00:23,000\nthis is the best option\n",
                encoding="utf-8",
            )
            run_script(
                "normalize_transcript.py",
                str(job),
                "--input",
                str(srt),
                "--kind",
                "asr",
            )
            normalized = (job / "transcript" / "asr_normalized.srt").read_text(
                encoding="utf-8"
            )
            self.assertIn("它叫做 BEST", normalized)
            self.assertIn("this is the best option", normalized)
            rows = [
                json.loads(line)
                for line in (job / "transcript" / "term_corrections.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual([row["applied"] for row in rows], [True, False])

    def test_access_note_rejects_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            result = run_script(
                "set_access_state.py",
                str(job),
                "--status",
                "authenticated",
                "--note",
                "cookie: TEST_ONLY_NOT_A_REAL_VALUE",
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("authentication secret", result.stderr)

    def test_evidence_coverage_counts_failed_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            run_script("manage_glossary.py", "confirm", str(job), "--by", "user", "--allow-empty")
            srt = Path(directory) / "subtitle.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:05,000\n第一段\n\n"
                "2\n00:00:05,000 --> 00:00:10,000\n第二段\n",
                encoding="utf-8",
            )
            run_script(
                "normalize_transcript.py",
                str(job),
                "--input",
                str(srt),
                "--kind",
                "subtitle",
            )
            frame_manifest = {
                "frames": [
                    {"frame_id": "F0001", "timestamp": 2.0, "selected_path": "frames/selected/F0001.jpg"},
                    {"frame_id": "F0002", "timestamp": 8.0, "selected_path": "frames/selected/F0002.jpg"},
                ]
            }
            (job / "frames" / "frame_manifest.json").write_text(
                json.dumps(frame_manifest), encoding="utf-8"
            )
            (job / "visual" / "visual_results.jsonl").write_text(
                json.dumps(
                    {
                        "frame_id": "F0001",
                        "timestamp": 2.0,
                        "analysis_status": "analyzed",
                        "summary": "第一帧",
                        "visual_verification": {
                            "verified": True,
                            "method": "human",
                            "reviewer": "test",
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "frame_id": "F0002",
                        "timestamp": 8.0,
                        "analysis_status": "failed",
                        "error": "test failure",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_script("build_evidence_timeline.py", str(job))
            coverage = read_json(job / "evidence" / "coverage.json")
            self.assertEqual(coverage["visual_coverage_ratio"], 0.5)
            self.assertEqual(coverage["missing_frame_ids"], ["F0002"])
            self.assertFalse(coverage["danmaku_used"])

    def test_frame_binds_to_single_nearest_segment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            run_script("manage_glossary.py", "confirm", str(job), "--by", "user", "--allow-empty")
            srt = Path(directory) / "subtitle.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:05,000\n开场介绍\n\n"
                "2\n00:00:40,000 --> 00:00:45,000\n后半段讲解\n",
                encoding="utf-8",
            )
            run_script(
                "normalize_transcript.py",
                str(job),
                "--input",
                str(srt),
                "--kind",
                "subtitle",
            )
            (job / "frames" / "frame_manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "frame_id": "F0001",
                                "timestamp": 2.0,
                                "selected_path": "frames/selected/F0001.jpg",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (job / "visual" / "visual_results.jsonl").write_text(
                json.dumps(
                    {
                        "frame_id": "F0001",
                        "timestamp": 2.0,
                        "analysis_status": "analyzed",
                        "summary": "开场画面",
                        "visual_verification": {
                            "verified": True,
                            "method": "human",
                            "reviewer": "test",
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            run_script("build_evidence_timeline.py", str(job))
            segments = [
                json.loads(line)
                for line in (job / "evidence" / "segments.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            attachments = [
                (segment["segment_id"], frame["frame_id"])
                for segment in segments
                for frame in segment.get("frames", [])
            ]
            # The frame is attached to exactly one segment, not both.
            self.assertEqual(attachments.count(("S0001", "F0001")), 1)
            self.assertEqual(len([a for a in attachments if a[1] == "F0001"]), 1)
            first = next(s for s in segments if s["segment_id"] == "S0001")
            attached_frame = first["frames"][0]
            self.assertEqual(attached_frame["representative_text"], "开场介绍")

    def test_audio_only_evidence_skips_visual_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory), evidence_mode="audio-only")
            config = read_json(job / "metadata" / "job.json")
            self.assertEqual(config["schema_version"], "3.0")
            self.assertEqual(config["policies"]["evidence_mode"], "audio_only")
            self.assertEqual(config["policies"]["cloud_vision"], "disabled")
            self.assertEqual(config["policies"]["note_illustrations"], "none")
            state = read_json(job / "metadata" / "pipeline_state.json")
            self.assertEqual(state["phases"]["keyframes"]["status"], "skipped")

            run_script("manage_glossary.py", "confirm", str(job), "--by", "user", "--allow-empty")
            srt = Path(directory) / "audio.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:05,000\n第一段对话\n\n"
                "2\n00:00:05,000 --> 00:00:10,000\n第二段对话\n",
                encoding="utf-8",
            )
            run_script(
                "normalize_transcript.py",
                str(job),
                "--input",
                str(srt),
                "--kind",
                "subtitle",
            )
            cloud_gate = run_script("audit_job.py", str(job), "--stage", "ready-for-cloud")
            self.assertIn("ready-for-cloud: PASS", cloud_gate.stdout)
            run_script("build_evidence_timeline.py", str(job))
            coverage = read_json(job / "evidence" / "coverage.json")
            self.assertEqual(coverage["evidence_mode"], "audio_only")
            self.assertEqual(coverage["transcript_coverage_ratio"], 1.0)
            self.assertIsNone(coverage["visual_coverage_ratio"])
            self.assertEqual(coverage["selected_frames"], 0)
            ready = run_script("audit_job.py", str(job), "--stage", "ready-for-note")
            self.assertIn("ready-for-note: PASS", ready.stdout)

            source_audio = job / "source" / "audio.m4a"
            source_audio.write_bytes(b"audio-source")
            asr_module = load_script_module("run_asr")
            self.assertEqual(asr_module.resolve_media(job, None), source_audio)

            (job / "note" / "note.md").write_text(
                "# 音频笔记\n\n> 音频优先整理，未核验画面内容。\n\n"
                "## 来源、覆盖与局限\n\n转录完整覆盖。\n",
                encoding="utf-8",
            )
            result = run_script("audit_job.py", str(job), "--stage", "final")
            self.assertIn("final: PASS", result.stdout)

    def test_audio_only_audit_rejects_image_embed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory), evidence_mode="audio-only")
            run_script("manage_glossary.py", "confirm", str(job), "--by", "user", "--allow-empty")
            srt = Path(directory) / "audio.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:05,000\n对话内容\n", encoding="utf-8")
            run_script(
                "normalize_transcript.py",
                str(job),
                "--input",
                str(srt),
                "--kind",
                "subtitle",
            )
            run_script("build_evidence_timeline.py", str(job))
            (job / "note" / "note.md").write_text(
                "# 音频笔记\n\n> 音频优先整理，未核验画面内容。\n\n"
                "![不应出现](https://example.com/image.png)\n\n"
                "![[不应出现.png]]\n\n"
                "<img src=\"https://example.com/image.png\">\n\n"
                "## 来源、覆盖与局限\n\n转录完整覆盖。\n",
                encoding="utf-8",
            )
            result = run_script("audit_job.py", str(job), "--stage", "final", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL audio_only_note_has_no_image_embeds", result.stdout)

    def test_audio_only_blocks_visual_commands_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory), evidence_mode="audio-only")
            keyframes = run_script(
                "select_keyframes.py",
                str(job),
                "--video",
                str(job / "source" / "video.mp4"),
                check=False,
            )
            self.assertNotEqual(keyframes.returncode, 0)
            self.assertIn("does not permit keyframe extraction", keyframes.stderr)
            crop = run_script("crop_illustrations.py", str(job), check=False)
            self.assertNotEqual(crop.returncode, 0)
            self.assertIn("does not permit illustration cropping", crop.stderr)

            run_script("manage_glossary.py", "confirm", str(job), "--by", "user", "--allow-empty")
            srt = Path(directory) / "audio.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:05,000\n对话内容\n", encoding="utf-8")
            run_script(
                "normalize_transcript.py",
                str(job),
                "--input",
                str(srt),
                "--kind",
                "subtitle",
            )
            run_script("build_evidence_timeline.py", str(job))
            unwanted = job / "frames" / "selected" / "unwanted.jpg"
            unwanted.write_bytes(b"unexpected-visual-artifact")
            audit = run_script("audit_job.py", str(job), "--stage", "ready-for-note", check=False)
            self.assertNotEqual(audit.returncode, 0)
            self.assertIn("FAIL audio_only_has_no_visual_artifacts", audit.stdout)

    def test_final_audit_passes_with_complete_local_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            run_script("manage_glossary.py", "confirm", str(job), "--by", "user", "--allow-empty")
            srt = Path(directory) / "subtitle.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:05,000\n完整证据段\n",
                encoding="utf-8",
            )
            run_script(
                "normalize_transcript.py",
                str(job),
                "--input",
                str(srt),
                "--kind",
                "subtitle",
            )
            frame_path = job / "frames" / "selected" / "F0001.jpg"
            frame_path.write_bytes(b"test-frame")
            (job / "frames" / "frame_manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "frame_id": "F0001",
                                "timestamp": 2.0,
                                "selected_path": "frames/selected/F0001.jpg",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (job / "visual" / "visual_results.jsonl").write_text(
                json.dumps(
                    {
                        "frame_id": "F0001",
                        "timestamp": 2.0,
                        "analysis_status": "analyzed",
                        "summary": "关键示意图",
                        "visual_verification": {
                            "verified": True,
                            "method": "human",
                            "reviewer": "test",
                        },
                        "illustrations": [
                            {
                                "region_id": "R01",
                                "kind": "diagram",
                                "bbox_norm": [0.1, 0.1, 0.8, 0.8],
                                "caption": "关键示意图",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            illustration = job / "illustrations" / "I0001_F0001_diagram.jpg"
            illustration.write_bytes(b"cropped-illustration")
            (job / "illustrations" / "manifest.json").write_text(
                json.dumps(
                    {
                        "candidate_count": 1,
                        "created_count": 1,
                        "failed_count": 0,
                        "items": [
                            {
                                "illustration_id": "I0001",
                                "frame_id": "F0001",
                                "timestamp": 2.0,
                                "region_id": "R01",
                                "caption": "关键示意图",
                                "source_path": "frames/selected/F0001.jpg",
                                "status": "created",
                                "path": "illustrations/I0001_F0001_diagram.jpg",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            run_script("build_evidence_timeline.py", str(job))
            (job / "evidence" / "illustration_bindings.jsonl").write_text(
                json.dumps(
                    {
                        "illustration_id": "I0001",
                        "frame_id": "F0001",
                        "timestamp": 2.0,
                        "segment_id": "S0001",
                        "caption": "关键示意图",
                        "note_anchor": "关键示意图",
                        "verified": True,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (job / "note" / "note.md").write_text(
                "# 笔记\n\n这段讲解对应下面的关键示意图。\n\n"
                "![关键示意图](../illustrations/I0001_F0001_diagram.jpg)\n\n"
                "## 来源、覆盖与局限\n\n完整覆盖。\n",
                encoding="utf-8",
            )
            result = run_script("audit_job.py", str(job), "--stage", "final")
            self.assertIn("final: PASS", result.stdout)

    def test_final_audit_rejects_full_frame_note_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            run_script("manage_glossary.py", "confirm", str(job), "--by", "user", "--allow-empty")
            srt = Path(directory) / "subtitle.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:05,000\n完整证据段\n",
                encoding="utf-8",
            )
            run_script(
                "normalize_transcript.py",
                str(job),
                "--input",
                str(srt),
                "--kind",
                "subtitle",
            )
            frame_path = job / "frames" / "selected" / "F0001.jpg"
            frame_path.write_bytes(b"full-frame")
            (job / "frames" / "frame_manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "frame_id": "F0001",
                                "timestamp": 2.0,
                                "selected_path": "frames/selected/F0001.jpg",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (job / "visual" / "visual_results.jsonl").write_text(
                json.dumps(
                    {
                        "frame_id": "F0001",
                        "timestamp": 2.0,
                        "analysis_status": "analyzed",
                        "summary": "完整画面",
                        "visual_verification": {
                            "verified": True,
                            "method": "human",
                            "reviewer": "test",
                        },
                        "illustrations": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (job / "illustrations" / "manifest.json").write_text(
                json.dumps(
                    {
                        "candidate_count": 0,
                        "created_count": 0,
                        "failed_count": 0,
                        "items": [],
                    }
                ),
                encoding="utf-8",
            )
            run_script("build_evidence_timeline.py", str(job))
            (job / "note" / "note.md").write_text(
                "# 笔记\n\n![整帧](../frames/selected/F0001.jpg)\n\n## 来源、覆盖与局限\n\n完整覆盖。\n",
                encoding="utf-8",
            )
            result = run_script("audit_job.py", str(job), "--stage", "final", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL note_uses_only_cropped_illustrations", result.stdout)

    def test_danmaku_artifact_fails_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            self.add_and_confirm_best(job)
            (job / "source" / "danmaku.xml").write_text("not allowed", encoding="utf-8")
            result = run_script(
                "audit_job.py", str(job), "--stage", "preflight", check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL no_danmaku_artifacts", result.stdout)

    def test_comment_or_live_chat_artifact_fails_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_youtube_job(Path(directory))
            (job / "source" / "live_chat.json").write_text("not allowed", encoding="utf-8")
            result = run_script(
                "audit_job.py", str(job), "--stage", "preflight", check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL no_comment_or_live_chat_artifacts", result.stdout)

    def test_public_metadata_is_sanitized(self) -> None:
        module = load_script_module("acquire_public")
        sanitized = module.sanitized_metadata(
            {
                "id": "BV0000000000",
                "title": "Example",
                "formats": [{"url": "https://signed.example/media"}],
                "comments": [{"text": "chat"}],
                "subtitles": {"zh-CN": [], "danmaku": []},
            }
        )
        self.assertNotIn("formats", sanitized)
        self.assertNotIn("comments", sanitized)
        self.assertNotIn("danmaku", sanitized["subtitle_languages"])
        self.assertFalse(sanitized["danmaku_stored"])

        with patch.object(module, "yt_dlp", return_value="/usr/local/bin/yt-dlp"):
            command = module.public_yt_dlp_args()
        self.assertIn("--ignore-config", command)
        self.assertIn("--no-cookies", command)
        self.assertIn("--no-cookies-from-browser", command)
        self.assertIn("--no-write-comments", command)
        self.assertIn("--no-playlist", command)
        self.assertIn("--no-write-playlist-metafiles", command)
        self.assertEqual(module.source_download_format(True), "ba")
        self.assertEqual(module.source_download_format(False), "bv*+ba/b")

    def test_youtube_subtitle_selection_prefers_original_human_then_auto(self) -> None:
        module = load_script_module("acquire_public")
        human = module.select_subtitle_track(
            {
                "language": "en",
                "human_subtitle_languages": ["en", "zh-Hans"],
                "automatic_caption_languages": ["en-orig", "zh-Hans"],
            }
        )
        self.assertEqual(human, {"kind": "human", "language": "en"})

        automatic = module.select_subtitle_track(
            {
                "language": "en",
                "human_subtitle_languages": [],
                "automatic_caption_languages": ["en", "en-orig", "zh-Hans"],
            }
        )
        self.assertEqual(automatic, {"kind": "automatic", "language": "en-orig"})
        self.assertIsNone(
            module.select_subtitle_track(
                {"human_subtitle_languages": [], "automatic_caption_languages": []}
            )
        )
        self.assertTrue(
            module.youtube_live_is_unsupported(
                {"source": {"platform": "youtube", "id": "abcdefghijk"}},
                {"id": "abcdefghijk", "live_status": "is_live"},
            )
        )
        self.assertFalse(
            module.youtube_live_is_unsupported(
                {"source": {"platform": "youtube", "id": "abcdefghijk"}},
                {"id": "abcdefghijk", "live_status": "was_live"},
            )
        )

    def test_youtube_frontmatter_and_output_defaults(self) -> None:
        module = load_script_module("publish_note")
        job = {
            "source": {
                "platform": "youtube",
                "id": "abcdefghijk",
                "url": "https://www.youtube.com/watch?v=abcdefghijk",
            },
            "policies": {"evidence_mode": "audio_only"},
        }
        text = module.yaml_frontmatter(
            job,
            {
                "id": "abcdefghijk",
                "title": "Example",
                "channel": "Example Channel",
                "upload_date": "20260826",
            },
        )
        self.assertIn('type: "youtube-audio-note"', text)
        self.assertIn('platform: "youtube"', text)
        self.assertIn('video_id: "abcdefghijk"', text)
        self.assertIn('channel: "Example Channel"', text)
        self.assertIn("  - YouTube", text)
        self.assertNotIn("bvid:", text)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_dir = self.make_youtube_job(root, evidence_mode="audio-only")
            run_script(
                "configure_output.py",
                str(job_dir),
                "--format",
                "markdown",
                "--note-dir",
                str(root / "notes"),
                "--asset-dir",
                str(root / "assets"),
            )
            config = read_json(job_dir / "output" / "output_config.json")
            self.assertEqual(config["filename_template"], "{upload_date}-{title}-{source_id}.md")
            self.assertEqual(config["asset_namespace"], "{source_id}")

    def test_keyframe_budget_helpers(self) -> None:
        module = load_script_module("select_keyframes")
        self.assertEqual(module.hamming_distance("0" * 64, "0" * 63 + "1"), 1)
        rows = [
            {"source": "scene" if index % 2 == 0 else "interval", "timestamp": float(index)}
            for index in range(20)
        ]
        selected = module.select_budget(rows, 7)
        self.assertEqual(len(selected), 7)
        self.assertEqual(selected, sorted(selected, key=lambda row: row["timestamp"]))

    def test_illustration_bbox_rejects_full_frame(self) -> None:
        module = load_script_module("crop_illustrations")
        bbox = module.validate_bbox([0.1, 0.2, 0.8, 0.7])
        self.assertEqual(bbox, [0.1, 0.2, 0.8, 0.7])
        self.assertIn("crop=iw*", module.build_crop_filter(bbox))
        with self.assertRaisesRegex(ValueError, "full-frame screenshot"):
            module.validate_bbox([0.0, 0.0, 1.0, 1.0])

    def test_crop_illustrations_creates_real_image(self) -> None:
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is not installed")
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            frame = job / "frames" / "selected" / "F0001.ppm"
            width = 40
            height = 30
            pixels = bytes([60, 120, 220]) * width * height
            frame.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixels)
            (job / "frames" / "frame_manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "frame_id": "F0001",
                                "timestamp": 1.0,
                                "selected_path": "frames/selected/F0001.ppm",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (job / "visual" / "visual_results.jsonl").write_text(
                json.dumps(
                    {
                        "frame_id": "F0001",
                        "timestamp": 1.0,
                        "analysis_status": "analyzed",
                        "summary": "测试图表",
                        "visual_verification": {
                            "verified": True,
                            "method": "human",
                            "reviewer": "test",
                        },
                        "illustrations": [
                            {
                                "region_id": "R01",
                                "kind": "chart",
                                "bbox_norm": [0.1, 0.1, 0.8, 0.8],
                                "caption": "测试图表",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            run_script("crop_illustrations.py", str(job))
            manifest = read_json(job / "illustrations" / "manifest.json")
            self.assertEqual(manifest["created_count"], 1)
            output = job / manifest["items"][0]["path"]
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)

    def test_obsidian_publish_copies_crops_and_cleans_managed_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = self.make_job(root)
            illustration = job / "illustrations" / "I0001_F0001_diagram.jpg"
            illustration.write_bytes(b"cropped-image")
            (job / "note" / "note.md").write_text(
                "# 测试笔记\n\n![结构图](../illustrations/I0001_F0001_diagram.jpg)\n\n## 来源、覆盖与局限\n\n完整覆盖。\n",
                encoding="utf-8",
            )
            (job / "metadata" / "audit_final.json").write_text(
                json.dumps(
                    {
                        "passed": True,
                        "checks": [
                            {"name": "visual_evidence_integrity", "passed": True}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (job / "metadata" / "audit_visual.json").write_text(
                json.dumps({"passed": True}), encoding="utf-8"
            )
            (job / "metadata" / "source_metadata.json").write_text(
                json.dumps(
                    {
                        "id": "BV0000000000",
                        "title": "测试视频",
                        "uploader": "测试UP",
                        "upload_date": "20260721",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            video = job / "source" / "video.mp4"
            audio = job / "transcript" / "asr_audio.wav"
            unmanaged = job / "source" / "manual-copy.mp4"
            video.write_bytes(b"managed-video")
            audio.write_bytes(b"managed-audio")
            unmanaged.write_bytes(b"unmanaged-video")
            run_script(
                "register_managed_media.py",
                str(job),
                "--path",
                "source/video.mp4",
                "--kind",
                "source_video",
                "--origin",
                "test",
            )
            run_script(
                "register_managed_media.py",
                str(job),
                "--path",
                "transcript/asr_audio.wav",
                "--kind",
                "derived_audio",
                "--origin",
                "test",
            )

            vault = root / "vault"
            (vault / ".obsidian").mkdir(parents=True)
            note_dir = vault / "Notes" / "Bilibili"
            asset_dir = vault / "Attachments" / "Bilibili"
            run_script(
                "configure_output.py",
                str(job),
                "--format",
                "obsidian",
                "--note-dir",
                str(note_dir),
                "--asset-dir",
                str(asset_dir),
                "--cleanup-media",
            )
            published = run_script("publish_note.py", str(job))
            note_path = Path(published.stdout.strip())
            note_text = note_path.read_text(encoding="utf-8")
            self.assertIn("bvid: \"BV0000000000\"", note_text)
            self.assertIn("![[Attachments/Bilibili/BV0000000000/", note_text)
            publish_manifest = read_json(job / "output" / "publish_manifest.json")
            self.assertTrue(publish_manifest["audit"]["passed"])
            self.assertEqual(len(publish_manifest["assets"]), 1)
            self.assertTrue(Path(publish_manifest["assets"][0]["destination"]).is_file())
            self.assertFalse(video.exists())
            self.assertFalse(audio.exists())
            self.assertTrue(unmanaged.exists())
            cleanup = read_json(job / "output" / "cleanup_report.json")
            self.assertEqual(cleanup["status"], "deleted")
            self.assertEqual(len(cleanup["files"]), 2)

    def test_audio_only_publish_allows_no_assets_and_cleans_source_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = self.make_job(root, evidence_mode="audio-only")
            (job / "note" / "note.md").write_text(
                "# 音频笔记\n\n> 音频优先整理，未核验画面内容。\n\n"
                "## 来源、覆盖与局限\n\n转录完整覆盖。\n",
                encoding="utf-8",
            )
            (job / "metadata" / "audit_final.json").write_text(
                json.dumps({"passed": True}), encoding="utf-8"
            )
            (job / "metadata" / "audit_visual.json").write_text(
                json.dumps({"passed": True}), encoding="utf-8"
            )
            (job / "metadata" / "source_metadata.json").write_text(
                json.dumps(
                    {
                        "id": "BV0000000000",
                        "title": "测试音频对话",
                        "uploader": "测试UP",
                        "upload_date": "20260721",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            source_audio = job / "source" / "audio.m4a"
            asr_audio = job / "transcript" / "asr_audio.wav"
            source_audio.write_bytes(b"managed-source-audio")
            asr_audio.write_bytes(b"managed-derived-audio")
            run_script(
                "register_managed_media.py",
                str(job),
                "--path",
                "source/audio.m4a",
                "--kind",
                "source_audio",
                "--origin",
                "test",
            )
            run_script(
                "register_managed_media.py",
                str(job),
                "--path",
                "transcript/asr_audio.wav",
                "--kind",
                "derived_audio",
                "--origin",
                "test",
            )

            vault = root / "vault"
            (vault / ".obsidian").mkdir(parents=True)
            run_script(
                "configure_output.py",
                str(job),
                "--format",
                "obsidian",
                "--note-dir",
                str(vault / "Notes" / "Bilibili"),
                "--asset-dir",
                str(vault / "Attachments" / "Bilibili"),
                "--cleanup-media",
            )
            published = run_script("publish_note.py", str(job))
            note_path = Path(published.stdout.strip())
            note_text = note_path.read_text(encoding="utf-8")
            self.assertIn('type: "bilibili-audio-note"', note_text)
            self.assertIn('evidence_mode: "audio_only"', note_text)
            manifest = read_json(job / "output" / "publish_manifest.json")
            self.assertEqual(manifest["assets"], [])
            self.assertTrue(manifest["audit"]["passed"])
            self.assertFalse(source_audio.exists())
            self.assertFalse(asr_audio.exists())

    def test_obsidian_output_requires_one_vault(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = self.make_job(root)
            vault_a = root / "vault-a"
            vault_b = root / "vault-b"
            (vault_a / ".obsidian").mkdir(parents=True)
            (vault_b / ".obsidian").mkdir(parents=True)
            result = run_script(
                "configure_output.py",
                str(job),
                "--format",
                "obsidian",
                "--note-dir",
                str(vault_a / "Notes"),
                "--asset-dir",
                str(vault_b / "Attachments"),
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("same Obsidian vault", result.stderr)

    def test_cleanup_requires_publish_audit_and_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = self.make_job(root)
            target = job / "source" / "target.mp4"
            link = job / "source" / "linked.mp4"
            target.write_bytes(b"video")
            link.symlink_to(target)
            register = run_script(
                "register_managed_media.py",
                str(job),
                "--path",
                "source/linked.mp4",
                "--kind",
                "source_video",
                "--origin",
                "test",
                check=False,
            )
            self.assertNotEqual(register.returncode, 0)
            self.assertIn("must not be a symlink", register.stderr)

            run_script(
                "register_managed_media.py",
                str(job),
                "--path",
                "source/target.mp4",
                "--kind",
                "source_video",
                "--origin",
                "test",
            )
            cleanup = run_script("cleanup_job.py", str(job), check=False)
            self.assertNotEqual(cleanup.returncode, 0)
            self.assertIn("requires passing final-note and published-note audits", cleanup.stderr)
            self.assertTrue(target.exists())

    def test_asr_context_and_timestamp_conversion(self) -> None:
        module = load_script_module("run_asr")
        context = module.build_asr_context(
            {
                "topic": "核聚变 托卡马克",
                "initial_prompt": "BEST means Burning plasma Experimental Superconducting Tokamak",
                "hotwords": ["BEST", "Burning plasma Experimental Superconducting Tokamak"],
            }
        )
        self.assertIn("Topic: 核聚变 托卡马克", context)
        self.assertIn("Preserve these exact specialist terms", context)
        self.assertIn("BEST", context)

        cues = module.segments_to_cues(
            {
                "segments": [
                    {"start": 0.0, "end": 30.0, "text": "第一段"},
                    {"start": 30.0, "end": 58.4, "text": "第二段"},
                ]
            }
        )
        self.assertEqual([(cue.start, cue.end, cue.text) for cue in cues], [
            (0.0, 30.0, "第一段"),
            (30.0, 58.4, "第二段"),
        ])

    def test_qwen_backend_passes_context_to_model(self) -> None:
        module = load_script_module("qwen3_asr_backend")

        class Result:
            language = "Chinese"
            text = "Codex 测试"

        class Model:
            kwargs = None

            def transcribe(self, **kwargs):
                self.kwargs = kwargs
                return [Result()]

        model = Model()
        result = module.transcribe_one(
            model,
            Path("audio.wav"),
            "Chinese",
            "Preserve the exact term Codex.",
        )
        self.assertEqual(result["text"], "Codex 测试")
        self.assertEqual(model.kwargs["context"], "Preserve the exact term Codex.")
        self.assertIsNone(module.normalize_language("auto"))
        self.assertEqual(module.normalize_language("en"), "English")

    def test_environment_distinguishes_subtitle_and_asr_routes(self) -> None:
        module = load_script_module("check_environment")
        tools = {
            "python3": "/usr/bin/python3",
            "ffmpeg": "/usr/bin/ffmpeg",
            "ffprobe": "/usr/bin/ffprobe",
            "yt-dlp": "/usr/bin/yt-dlp",
        }
        without_asr = module.build_report(tools, None)
        self.assertTrue(without_asr["ready_for_subtitle_pipeline"])
        self.assertFalse(without_asr["ready_for_no_subtitle_pipeline"])
        self.assertFalse(without_asr["ready_for_full_pipeline"])
        self.assertFalse(without_asr["ready_for_youtube_public_pipeline"])

        tools["deno"] = "/usr/bin/deno"
        with_asr = module.build_report(tools, "/shared/qwen/bin/python")
        self.assertTrue(with_asr["ready_for_no_subtitle_pipeline"])
        self.assertTrue(with_asr["ready_for_full_pipeline"])
        self.assertTrue(with_asr["ready_for_youtube_public_pipeline"])

    def test_run_asr_requires_glossary_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            result = run_script("run_asr.py", str(job), check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("glossary must be explicitly confirmed", result.stderr)


if __name__ == "__main__":
    unittest.main()
