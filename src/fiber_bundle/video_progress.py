"""Read-only frame progress and bounded legacy diagnostics; never resume processing."""

import json
import sqlite3
from typing import Any

from . import video_call_allowance, video_token_budget
from .config import Config, StorageError, contained
from .query_routing_files import read
from .video_visual_contract import VisualContractError, json_reply


def project(
    db: sqlite3.Connection, frozen: dict[str, Any], config: Config | None = None
) -> dict[str, Any]:
    ids = list(frozen.get("vision_runs", {}).values())
    completed = 0
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        completed += db.execute(
            "SELECT count(*) FROM execution_runs WHERE state='succeeded' AND id IN ("
            + ",".join("?" for _ in part)
            + ")",
            part,
        ).fetchone()[0]
    total = frozen.get("frame_count")
    error = frozen.get("visual_response_error") or {}
    code = error.get("code")
    if config is not None and (total is None or (error and not code)):
        try:
            job = contained(config.state_root, "video-skill-jobs/" + frozen["job_id"])
            if total is None:
                total = len(json.loads(read(job / "frames/frame_manifest.json", 2**20))["frames"])
            if error and not code:
                response = json.loads(read(contained(job, error["response_path"]), 256 * 1024))
                if (
                    response["run_id"] == error["run_id"]
                    and response["frame_id"] == error["frame_id"]
                ):
                    try:
                        json_reply(response["body"])
                    except VisualContractError as exc:
                        code = exc.code
        except (OSError, ValueError, KeyError, TypeError, StorageError):
            pass  # The recorded failure remains visible when a legacy artifact is unavailable.
    return {
        "frames_total": total,
        "frames_complete": completed,
        "frames_remaining": max(0, total - completed) if isinstance(total, int) else None,
        "failed_frame": error.get("frame_id"),
        "failure_code": code,
        "token_budget": video_token_budget.status(db, video_token_budget.descriptor(frozen)),
        "call_allowance": video_call_allowance.status(db, frozen["call_allowance"])
        if frozen.get("call_allowance")
        else None,
    }
