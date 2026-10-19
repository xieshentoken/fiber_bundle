from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audit_visual_evidence import build_report  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


class VisualEvidenceTests(unittest.TestCase):
    def make_job(self, root: Path) -> Path:
        job = root / "job"
        (job / "frames" / "selected").mkdir(parents=True)
        (job / "visual").mkdir()
        (job / "illustrations").mkdir()
        (job / "evidence").mkdir()
        (job / "note").mkdir()
        for frame_id in ("F0001", "F0002"):
            (job / "frames" / "selected" / f"{frame_id}.jpg").write_bytes(frame_id.encode())
        write_json(
            job / "frames" / "frame_manifest.json",
            {
                "frames": [
                    {
                        "frame_id": "F0001",
                        "timestamp": 10.0,
                        "selected_path": "frames/selected/F0001.jpg",
                    },
                    {
                        "frame_id": "F0002",
                        "timestamp": 20.0,
                        "selected_path": "frames/selected/F0002.jpg",
                    },
                ]
            },
        )
        write_jsonl(
            job / "visual" / "visual_results.jsonl",
            [
                {
                    "frame_id": "F0001",
                    "timestamp": 10.0,
                    "analysis_status": "analyzed",
                    "summary": "显示关键结构图",
                    "visual_verification": {
                        "verified": True,
                        "method": "human",
                        "reviewer": "test-reviewer",
                    },
                    "illustrations": [
                        {
                            "region_id": "R01",
                            "kind": "diagram",
                            "caption": "关键结构图",
                            "bbox_norm": [0.1, 0.1, 0.8, 0.8],
                        }
                    ],
                },
                {
                    "frame_id": "F0002",
                    "timestamp": 20.0,
                    "analysis_status": "not_needed",
                    "visual_verification": {
                        "verified": True,
                        "method": "human",
                        "reviewer": "test-reviewer",
                    },
                    "skip_reason_code": "duplicate",
                    "duplicate_of": "F0001",
                },
            ],
        )
        crop = job / "illustrations" / "I0001_F0001_diagram.jpg"
        crop.write_bytes(b"crop")
        write_json(
            job / "illustrations" / "manifest.json",
            {
                "candidate_count": 1,
                "created_count": 1,
                "failed_count": 0,
                "items": [
                    {
                        "illustration_id": "I0001",
                        "frame_id": "F0001",
                        "timestamp": 10.0,
                        "region_id": "R01",
                        "caption": "关键结构图",
                        "source_path": "frames/selected/F0001.jpg",
                        "status": "created",
                        "path": "illustrations/I0001_F0001_diagram.jpg",
                    }
                ],
            },
        )
        write_jsonl(
            job / "evidence" / "segments.jsonl",
            [
                {
                    "segment_id": "S0001",
                    "start": 9.0,
                    "end": 12.0,
                    "frames": [
                        {
                            "frame_id": "F0001",
                            "timestamp": 10.0,
                            "path": "frames/selected/F0001.jpg",
                        }
                    ],
                }
            ],
        )
        write_jsonl(
            job / "evidence" / "illustration_bindings.jsonl",
            [
                {
                    "illustration_id": "I0001",
                    "frame_id": "F0001",
                    "timestamp": 10.0,
                    "segment_id": "S0001",
                    "caption": "关键结构图",
                    "note_anchor": "关键结构图",
                    "verified": True,
                }
            ],
        )
        (job / "note" / "note.md").write_text(
            "# Note\n\n下面这张关键结构图展示了核心结构。\n\n"
            "![关键结构图](../illustrations/I0001_F0001_diagram.jpg)\n",
            encoding="utf-8",
        )
        return job

    def test_verified_rows_and_bindings_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = build_report(
                self.make_job(Path(directory)),
                require_illustrations=True,
                require_bindings=True,
                require_note=True,
            )
            self.assertTrue(report["passed"], report)

    def test_placement_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            # The placement map binds I0001 to S0002, but the binding claims
            # S0001.  This is the "image attached to the wrong claim" case.
            write_json(
                job / "evidence" / "illustration_placement.json",
                {
                    "schema_version": "1.0",
                    "items": [
                        {"illustration_id": "I0001", "primary_segment_id": "S0002"}
                    ],
                },
            )
            report = build_report(
                job,
                require_illustrations=True,
                require_bindings=True,
                require_note=True,
            )
            self.assertFalse(report["passed"], report)
            placement_check = next(
                check
                for check in report["checks"]
                if check["name"] == "illustration_placement_consistency"
            )
            self.assertFalse(placement_check["passed"])
            self.assertIn("S0002", placement_check["detail"])

    def test_unembedded_illustration_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            # A created crop that the note never embeds must not pass.
            (job / "note" / "note.md").write_text(
                "# Note\n\n正文没有引用这张图。\n", encoding="utf-8"
            )
            report = build_report(
                job,
                require_illustrations=True,
                require_bindings=True,
                require_note=True,
            )
            self.assertFalse(report["passed"], report)
            placement_check = next(
                check
                for check in report["checks"]
                if check["name"] == "note_illustration_placement"
            )
            self.assertFalse(placement_check["passed"])
            self.assertIn("does not embed", placement_check["detail"])

    def test_timestamp_mismatch_and_manual_analysis_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            path = job / "visual" / "visual_results.jsonl"
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            rows[0]["timestamp"] = 0.0
            rows[0]["cloud_provider"] = "manual-analysis"
            path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            report = build_report(Path(directory) / "job")
            self.assertFalse(report["passed"])
            detail = report["checks"][0]["detail"]
            self.assertIn("timestamp", detail)
            self.assertIn("manual-analysis", detail)


if __name__ == "__main__":
    unittest.main()
