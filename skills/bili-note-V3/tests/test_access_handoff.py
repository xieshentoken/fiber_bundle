from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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


class AccessHandoffTests(unittest.TestCase):
    def make_job(self, root: Path) -> Path:
        job = root / "job"
        run_script(
            "init_job.py",
            "https://www.bilibili.com/video/BV0000000000/",
            "--out",
            str(job),
            "--topic",
            "test",
        )
        return job

    def test_preview_duration_is_not_complete(self) -> None:
        import common

        preview = common.source_coverage_report(1068.445, 315.002)
        self.assertFalse(preview["complete"])
        self.assertEqual(preview["state"], "preview_limited")
        self.assertAlmostEqual(preview["coverage_ratio"], 0.2948, places=4)

        complete = common.source_coverage_report(1068.445, 1066.2)
        self.assertTrue(complete["complete"])
        self.assertEqual(complete["state"], "complete")

    def test_edge_request_and_non_secret_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            request = run_script(
                "browser_handoff.py",
                "request",
                str(job),
                "--backend",
                "edge",
            )
            self.assertIn('"backend": "edge"', request.stdout)
            access = read_json(job / "metadata" / "access.json")
            self.assertEqual(access["access_state"], "auth_required")
            self.assertEqual(access["handoff_state"], "pending")

            record = run_script(
                "browser_handoff.py",
                "record",
                str(job),
                "--backend",
                "edge",
                "--access-state",
                "member_authorized",
                "--near-end",
            )
            self.assertIn('"handoff_state": "unavailable"', record.stdout)
            access = read_json(job / "metadata" / "access.json")
            self.assertEqual(access["access_state"], "member_authorized")
            self.assertEqual(access["handoff_state"], "unavailable")
            self.assertFalse(access["secrets_stored"])

    def test_youtube_request_uses_canonical_single_video_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / "youtube-job"
            run_script(
                "init_job.py",
                "https://youtu.be/abcdefghijk?t=30",
                "--out",
                str(job),
            )
            result = run_script(
                "browser_handoff.py",
                "request",
                str(job),
                "--backend",
                "chrome",
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["backend"], "chrome")
            self.assertEqual(
                payload["url"],
                "https://www.youtube.com/watch?v=abcdefghijk",
            )

    def test_browser_handoff_rejects_secret_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            result = run_script(
                "browser_handoff.py",
                "record",
                str(job),
                "--backend",
                "edge",
                "--access-state",
                "member_authorized",
                "--near-end",
                "--note",
                "cookie: should never be accepted",
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("authentication material", result.stderr)

    def test_member_entitlement_cannot_be_verified_manually(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            result = run_script(
                "set_access_state.py",
                str(job),
                "--status",
                "member_authorized",
                "--verified-by",
                "manual",
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be verified in a browser", result.stderr)

    def test_outcome_requires_final_audit_for_done(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            run_script("browser_handoff.py", "request", str(job), "--backend", "edge")
            result = run_script("write_task_outcome.py", str(job))
            self.assertIn('"status":"waiting_user_action"', result.stdout)
            outcome = read_json(job / "metadata" / "task_outcome.json")
            self.assertEqual(outcome["status"], "waiting_user_action")

    def test_job_gate_requires_resume_for_legacy_or_auth_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            access = read_json(job / "metadata" / "access.json")
            access.update(
                {
                    "access_state": "auth_required",
                    "status": "auth_required",
                    "source_state": "preview_limited",
                    "handoff_state": "pending",
                }
            )
            (job / "metadata" / "access.json").write_text(
                json.dumps(access, ensure_ascii=False), encoding="utf-8"
            )
            result = run_script("job_gate.py", str(job), "--json")
            report = json.loads(result.stdout)
            self.assertEqual(report["decision"], "waiting_user_action")
            self.assertEqual(report["resume_phase"], "access")
            self.assertFalse(report["reusable"])

    def test_access_preflight_creates_idempotent_chrome_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(Path(directory))
            access = read_json(job / "metadata" / "access.json")
            access.update(
                {
                    "access_state": "auth_required",
                    "status": "auth_required",
                    "source_state": "preview_limited",
                }
            )
            (job / "metadata" / "access.json").write_text(
                json.dumps(access, ensure_ascii=False), encoding="utf-8"
            )
            first = run_script(
                "access_preflight.py", str(job), "--backend", "chrome", "--json", check=False
            )
            self.assertEqual(first.returncode, 10)
            first_request = read_json(job / "metadata" / "browser_handoff_request.json")
            self.assertEqual(first_request["backend"], "chrome")

            second = run_script(
                "access_preflight.py", str(job), "--backend", "google-chrome", "--json", check=False
            )
            self.assertEqual(second.returncode, 10)
            second_request = read_json(job / "metadata" / "browser_handoff_request.json")
            self.assertEqual(second_request["backend"], "chrome")
            self.assertIn("Google Chrome", second.stdout)


if __name__ == "__main__":
    unittest.main()
