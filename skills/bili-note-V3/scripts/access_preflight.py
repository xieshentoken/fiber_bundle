#!/usr/bin/env python3
"""Run the deterministic access/resume gate before V3 extraction.

Exit codes are stable for orchestrators: 0 means extraction may continue,
10 means browser/user action is required, 20 means access is blocked, and 30
means the job cannot be inspected.  The command never reads browser state.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from browser_handoff import BACKENDS, request_handoff
from job_gate import inspect_job


EXIT_READY = 0
EXIT_WAITING_USER_ACTION = 10
EXIT_BLOCKED = 20
EXIT_FAILED = 30
BACKEND_ALIASES = {
    "edge": "edge",
    "msedge": "edge",
    "chrome": "chrome",
    "google-chrome": "chrome",
    "generic": "generic_browser",
    "generic_browser": "generic_browser",
}


def normalize_backend(value: str | None) -> str:
    raw = str(value or os.environ.get("BILI_NOTE_BROWSER_BACKEND") or "generic_browser")
    normalized = BACKEND_ALIASES.get(raw.strip().lower())
    if normalized not in BACKENDS:
        raise ValueError(
            f"Unsupported browser backend: {value}; choose edge, chrome, or generic_browser"
        )
    return normalized


def existing_request(job_dir: Path, backend: str) -> dict[str, Any] | None:
    path = job_dir / "metadata" / "browser_handoff_request.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("state") == "pending" and value.get("backend") == backend:
        return value
    return None


def build_result(
    report: dict[str, Any],
    *,
    decision: str,
    backend: str | None = None,
    request: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    result = {
        "schema_version": "1.0",
        "decision": decision,
        "reason": report.get("reason"),
        "resume_phase": report.get("resume_phase"),
        "job": {
            "platform": report.get("platform"),
            "source_id": report.get("source_id"),
            "bvid": report.get("bvid"),
            "legacy_job": report.get("legacy_job"),
            "source_coverage_complete": report.get("source_coverage_complete"),
            "final_audit_passed": report.get("final_audit_passed"),
        },
        "browser_backend": backend,
        "request_state": request.get("state") if request else None,
        "action_required": None,
        "error": error,
        "secrets_stored": False,
    }
    if decision == "waiting_user_action":
        backend_label = {
            "edge": "Microsoft Edge",
            "chrome": "Google Chrome",
            "generic_browser": "Edge 或 Chrome",
        }.get(backend or "", "浏览器")
        result["action_required"] = (
            f"请通过已登录的{backend_label}打开目标视频，完成可播放和接近末尾校验，"
            "再由浏览器连接器提交当前 Job 的本地媒体副本；不要导出 Cookie、Local Storage、授权头或签名 URL。"
        )
    return result


def run(job_dir: Path, backend: str) -> tuple[int, dict[str, Any]]:
    report = inspect_job(job_dir)
    decision = str(report.get("decision") or "failed")
    if decision == "reusable":
        return EXIT_READY, build_result(report, decision="reusable")
    if decision == "blocked":
        return EXIT_BLOCKED, build_result(report, decision="blocked")
    if decision == "failed":
        return EXIT_FAILED, build_result(
            report, decision="failed", error="job metadata is missing or invalid"
        )
    if decision in {"continue_public_probe", "resume_pipeline"}:
        return EXIT_READY, build_result(report, decision=decision)

    request = existing_request(job_dir, backend)
    if request is None:
        request = request_handoff(job_dir, backend)
    return EXIT_WAITING_USER_ACTION, build_result(
        report,
        decision="waiting_user_action",
        backend=backend,
        request=request,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument(
        "--backend",
        default=None,
        help="edge, chrome, or generic_browser; aliases msedge/google-chrome/generic are accepted",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    try:
        backend = normalize_backend(args.backend)
        code, result = run(args.job.expanduser().resolve(), backend)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {
            "schema_version": "1.0",
            "decision": "failed",
            "reason": "preflight_error",
            "action_required": None,
            "error": str(exc),
            "secrets_stored": False,
        }
        code = EXIT_FAILED
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"decision={result.get('decision')}")
        print(f"reason={result.get('reason')}")
        if result.get("action_required"):
            print(f"action_required={result['action_required']}")
        if result.get("error"):
            print(f"error={result['error']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
