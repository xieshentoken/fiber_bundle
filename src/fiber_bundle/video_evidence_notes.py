"""Explicit, model-free note recovery from already audited visual source material."""

import html
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .config import InvalidInput, contained
from .objects import digest

MODE = "visual_evidence"


def require_cached(db: sqlite3.Connection, frozen: dict[str, Any], job: Path | None = None) -> None:
    """Missing or changed evidence must never trigger another inference as a fallback."""
    required = {
        "initialize",
        "terms",
        "public_metadata",
        "media",
        "subtitle_attempt",
        "transcript",
        "frames",
        "crops",
        "timeline",
    }
    runs = frozen.get("vision_runs", {})
    if (
        frozen.get("transcript_route") != "visual_primary"
        or not required.issubset(frozen.get("completed", []))
        or not runs
    ):
        raise InvalidInput("video_skill_visual_audit_failed")
    for rid in runs.values():
        row = db.execute("SELECT state FROM execution_runs WHERE id=?", (rid,)).fetchone()
        if row is None or row[0] != "succeeded":
            raise InvalidInput("video_skill_visual_audit_failed")
    if job is None:
        return
    try:
        frames = json.loads((job / "frames/frame_manifest.json").read_bytes())["frames"]
        rows = [
            json.loads(line)
            for line in (job / "visual/visual_results.jsonl").read_text().splitlines()
            if line
        ]
        by_id = {row["frame_id"]: row for row in rows}
        if len(by_id) != len(rows) or len(rows) != len(frames) or set(by_id) != set(runs):
            raise ValueError()
        for frame in frames:
            fid = frame["frame_id"]
            row = by_id[fid]
            if (
                row != json.loads(contained(job, "visual/" + fid + ".json").read_bytes())
                or row["run_id"] != runs[fid]
                or row["visual_verification"]["input_sha256"] != frame["sha256"]
                or digest(contained(job, frame["selected_path"]).read_bytes()) != frame["sha256"]
            ):
                raise ValueError()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InvalidInput("video_skill_visual_audit_failed") from error


def literal(value: str) -> str:
    # Source text is displayed literally; it cannot inject HTML or image references.
    return re.sub(r"([\\`*_{}\[\]()!#|])", r"\\\1", html.escape(value, quote=False))


def segment_note(evidence: dict[str, Any]) -> str:
    parts: list[str] = []
    useful = False
    for frame in evidence["visual_evidence"]:
        analysis = frame["analysis"]
        for key in ("ocr_text", "claims"):
            items = analysis.get(key)
            if not isinstance(items, list) or any(not isinstance(x, str) for x in items):
                raise InvalidInput("video_skill_visual_content_missing")
            useful |= any(x.strip() for x in items)
        parts += ["", f"画面时间点：{frame['timestamp']:.3f} 秒", ""]
        if analysis.get("summary"):
            if not isinstance(analysis["summary"], str):
                raise InvalidInput("video_skill_visual_content_missing")
            parts += ["画面分析摘要：" + literal(analysis["summary"]), ""]
        if analysis["ocr_text"]:
            parts += ["画面文字（OCR，未人工校对）：", ""]
            for text in analysis["ocr_text"]:
                parts += ["> " + literal(line) for line in text.splitlines()]
            parts += [""]
        if analysis["claims"]:
            parts += ["画面分析要点（模型提取，未独立核验）：", ""]
            parts += ["- " + literal(text).replace("\n", " ") for text in analysis["claims"]]
    if not useful:
        raise InvalidInput("video_skill_visual_content_missing")
    return "\n".join(parts)
