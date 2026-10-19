#!/usr/bin/env python3
"""Derive the watcher-safe outcome marker for a Bili Note V3 job."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from common import canonical_access_state, read_json, utc_now, write_json


OUTCOME_STATUSES = {"done", "waiting_user_action", "blocked", "failed"}


def build_outcome(job_dir: Path) -> dict[str, Any]:
    access_path = job_dir / "metadata" / "access.json"
    access = read_json(access_path) if access_path.exists() else {}
    access_state = canonical_access_state(access)
    source_state = str(access.get("source_state") or "not_acquired")
    handoff_state = str(access.get("handoff_state") or "not_required")
    backend = access.get("browser_backend")
    final_path = job_dir / "metadata" / "audit_final.json"
    ready_path = job_dir / "metadata" / "audit_ready-for-note.json"
    final_audit = read_json(final_path) if final_path.exists() else {}
    ready_audit = read_json(ready_path) if ready_path.exists() else {}

    if final_audit.get("passed") is True:
        status = "done"
        reason = "final_audit_passed"
        audit_stage = "final"
        audit_passed = True
        action = None
    elif access_state in {"denied", "drm_blocked"}:
        status = "blocked"
        reason = access_state
        audit_stage = "final" if final_path.exists() else None
        audit_passed = False
        action = "当前页面拒绝访问或受到 DRM 保护；请停止自动提取并改用有权限的来源。"
    elif (
        access_state == "auth_required"
        or source_state in {"preview_limited", "partial", "unverified"}
        or handoff_state in {"pending", "unavailable", "failed"}
    ):
        status = "waiting_user_action"
        reason = source_state if source_state != "not_acquired" else "browser_handoff_required"
        audit_stage = "ready-for-note" if ready_path.exists() else None
        audit_passed = ready_audit.get("passed") is True if ready_path.exists() else False
        backend_labels = {
            "edge": "Microsoft Edge",
            "chrome": "Google Chrome",
            "generic_browser": "浏览器",
        }
        backend_label = backend_labels.get(str(backend), "Microsoft Edge 或 Google Chrome")
        if handoff_state == "unavailable":
            action = (
                "当前执行器未绑定可用的 Microsoft Edge/Google Chrome 浏览器连接器；"
                "请先启用浏览器连接器后，从同一 Job 恢复安全媒体交接。"
                "不要导出 Cookie、Local Storage、授权头或签名 URL。"
            )
        elif handoff_state == "failed":
            action = (
                f"{backend_label} 浏览器安全媒体交接失败；请在已登录页面确认目标视频可播放后重试。"
                "不要导出 Cookie、Local Storage、授权头或签名 URL。"
            )
        else:
            action = (
                f"请在已登录的 {backend_label} 页面打开目标视频，确认可播放并完成浏览器安全媒体交接；"
                "不要导出 Cookie、Local Storage、授权头或签名 URL。"
            )
    elif final_path.exists() and final_audit.get("passed") is False:
        status = "failed"
        reason = "final_audit_failed"
        audit_stage = "final"
        audit_passed = False
        action = "请修复 final 审计失败项后重新运行。"
    else:
        status = "failed"
        reason = "incomplete_pipeline"
        audit_stage = None
        audit_passed = False
        action = "V3 流程没有形成可审计的最终结果。"

    outcome = {
        "schema_version": "1.0",
        "status": status,
        "reason": reason,
        "access_state": access_state,
        "source_state": source_state,
        "handoff_state": handoff_state,
        "browser_backend": backend,
        "audit_stage": audit_stage,
        "audit_passed": audit_passed,
        "action_required": action,
        "generated_at": utc_now(),
    }
    return outcome


def marker(outcome: dict[str, Any]) -> str:
    payload = json.dumps(outcome, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"<!-- geof-task-outcome {payload} -->"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--no-write", action="store_true", help="Only print the marker")
    args = parser.parse_args()
    job_dir = args.job.expanduser().resolve()
    outcome = build_outcome(job_dir)
    if not args.no_write:
        write_json(job_dir / "metadata" / "task_outcome.json", outcome)
    print(marker(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
