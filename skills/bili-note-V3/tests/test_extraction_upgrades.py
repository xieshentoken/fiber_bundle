from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def load_script(name: str):
    path = SCRIPT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExtractionUpgradeTests(unittest.TestCase):
    def test_focus_and_cue_time_parsing(self) -> None:
        module = load_script("select_keyframes")
        self.assertEqual(module.parse_time("90"), 90.0)
        self.assertEqual(module.parse_time("01:30"), 90.0)
        self.assertEqual(module.parse_time("00:01:30.500"), 90.5)
        self.assertEqual(module.parse_timestamps("4:30,90,4:30"), [90.0, 270.0])

    def test_pinned_cues_are_reserved_from_budget(self) -> None:
        module = load_script("select_keyframes")
        rows = [
            {"source": "interval", "timestamp": 1.0},
            {"source": "scene", "timestamp": 2.0},
            {"source": "cue", "timestamp": 3.0, "pinned": True},
            {"source": "scene", "timestamp": 4.0},
        ]
        selected, over = module.select_with_pins(rows, 2)
        self.assertFalse(over)
        self.assertEqual([row["source"] for row in selected], ["cue", "scene"])

    def test_pinned_cues_can_exceed_budget_explicitly(self) -> None:
        module = load_script("select_keyframes")
        rows = [
            {"source": "cue", "timestamp": 3.0, "pinned": True},
            {"source": "cue", "timestamp": 4.0, "pinned": True},
        ]
        selected, over = module.select_with_pins(rows, 1)
        self.assertTrue(over)
        self.assertEqual(len(selected), 2)

    def test_batch_dedup_prefers_cue_and_scene(self) -> None:
        module = load_script("select_keyframes")
        rows = [
            {"source": "interval", "timestamp": 10.0, "candidate_path": "i.jpg"},
            {"source": "scene", "timestamp": 10.0, "candidate_path": "s.jpg"},
            {"source": "cue", "timestamp": 10.0, "candidate_path": "c.jpg"},
        ]
        with patch.object(module, "batch_average_hashes", return_value=["0" * 64] * 3):
            kept, dropped = module.deduplicate_rows(rows, interval=15.0, dedupe_hamming=2)
        self.assertEqual(dropped, 2)
        self.assertEqual(kept[0]["source"], "cue")

    def test_asr_boundary_overlap_and_gap_report(self) -> None:
        qwen = load_script("qwen3_asr_backend")
        self.assertEqual(qwen.trim_boundary_overlap("第一段内容", "内容第二段"), "第二段")

        run_asr = load_script("run_asr")
        cues = [
            run_asr.Cue(index=1, start=0.0, end=2.0, text="一"),
            run_asr.Cue(index=2, start=4.0, end=5.0, text="二"),
        ]
        report = run_asr.build_coverage_report(
            cues,
            6.0,
            [{"chunk_index": 1, "status": "failed"}],
        )
        self.assertEqual(report["failed_chunk_count"], 1)
        self.assertEqual(report["gap_ranges"], [{"start": 2.0, "end": 4.0}, {"start": 5.0, "end": 6.0}])


if __name__ == "__main__":
    unittest.main()
