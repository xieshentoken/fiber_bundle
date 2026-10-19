"""Segment progression, fixed Pi video authoring and truthful Inbox publication."""

import base64
import json
import sqlite3
from typing import Any

from . import (
    automatic_policy,
    database,
    execution,
    execution_ledger,
    pi_processing,
    publication_layout,
    video_contract,
    video_tasks,
    video_tools,
)
from .config import Config, InvalidInput, contained, make_directory
from .executor_container import run_lock
from .inputs import canonical
from .media_subtitles import normalized
from .objects import digest
from .query_policy_store import write_private
from .query_routing_files import read


def _guard(config: Config, cid: str, identity: str) -> None:
    with database.locked(config), database.connect(config.state_root) as db:
        if video_tasks.get(config, db, cid, identity)["state"] != "running":
            raise InvalidInput("video_task_stopped")


def run(config: Config, cid: str, identity: str) -> dict[str, Any]:
    with run_lock(config, identity):
        try:
            return advance(config, cid, identity)
        except Exception as error:
            code = str(error)
            from .processing_errors import MESSAGES

            if code not in MESSAGES:
                code = "video_processing_failed"
            with database.locked(config), database.connect(config.state_root) as db:
                row = db.execute("SELECT * FROM video_tasks WHERE id=?", (identity,)).fetchone()
                if row and row["state"] == "running":
                    value = json.loads(row["value"])
                    value["error_code"] = code
                    video_tasks.update(db, identity, "failed", value)
            raise


def advance(config: Config, cid: str, identity: str) -> dict[str, Any]:
    with database.locked(config), database.connect(config.state_root) as db:
        row = video_tasks.get(config, db, cid, identity)
        if row["state"] not in {"queued", "launching"}:
            return video_tasks.project(row)
        frozen, value = json.loads(row["policy_json"]), json.loads(row["value"])
        source, version = video_tasks.source(config, db, row)
        settings = frozen["vault_policy"]["settings"]
        video_tasks.update(db, identity, "running", value)
    for number, segment in enumerate(value["segments"]):
        _guard(config, cid, identity)
        spec = {
            **segment,
            "duration_ms": frozen["duration_ms"],
            "source_sha256": source.sha256,
            "format": frozen["format"],
            "frames": frozen["frames"],
            "subtitle": frozen["subtitle"],
            "glossary": frozen["glossary"],
            "asr_resources": frozen["resources"]["asr_resources"],
            "cues": [
                {
                    **cue,
                    "start_ms": max(cue["start_ms"], segment["start_ms"]) - segment["start_ms"],
                    "end_ms": min(cue["end_ms"], segment["end_ms"]) - segment["start_ms"],
                }
                for cue in frozen["cues"]
                if cue["end_ms"] > segment["start_ms"] and cue["start_ms"] < segment["end_ms"]
            ],
        }
        if segment["state"] != "complete":
            captured = video_tools.execute(
                config, source, spec, frozen["resources"], lambda: _guard(config, cid, identity)
            )
            segment.update(captured, state="complete")
            with database.locked(config), database.connect(config.state_root) as db:
                active = video_tasks.get(config, db, cid, identity)
                if active["state"] != "running":
                    raise InvalidInput("video_task_stopped")
                video_tasks.update(db, identity, "running", value)
        captured = segment["result"]
        if digest(canonical(captured).encode()) != segment["result_sha256"]:
            raise InvalidInput("video_segment_source_changed")
        video_contract.verify(captured, spec)
        if settings.get("processing_mode") == "pi_skill" and captured["cues"]:
            model = automatic_policy.processing_route(config, settings)
            with database.locked(config), database.connect(config.state_root) as db:
                video_tasks.get(config, db, cid, identity)
                if not segment.get("run_id"):
                    segment["run_id"] = pi_processing.prepare(
                        config,
                        db,
                        model,
                        cid,
                        row["vault_id"],
                        version,
                        "video",
                        source.sha256,
                        [
                            {
                                "id": f"video:{number}:{i}",
                                "start_ms": cue["start_ms"] + segment["start_ms"],
                                "end_ms": cue["end_ms"] + segment["start_ms"],
                                "text": cue["text"],
                            }
                            for i, cue in enumerate(captured["cues"])
                        ],
                        allow_cloud=settings["allow_model_source_content"],
                    )
                    video_tasks.update(db, identity, "running", value)
                rid = segment["run_id"]
                state = execution_ledger.get_run(db, rid)["state"]
            if state in {"queued", "waiting_budget"}:
                execution.execute(config, model, rid, "fixed video skill")
            _guard(config, cid, identity)
            with database.locked(config), database.connect(config.state_root) as db:
                state = execution_ledger.get_run(db, rid)["state"]
                if state != "succeeded":
                    value["error_code"] = (
                        "video_model_budget_required"
                        if state == "waiting_budget"
                        else "video_model_reconciliation_required"
                    )
                    video_tasks.update(
                        db,
                        identity,
                        "waiting_budget" if state == "waiting_budget" else "failed",
                        value,
                    )
                    return video_tasks.project(video_tasks.get(config, db, cid, identity))
                segment["analysis"] = pi_processing.result(config, db, rid)
                video_tasks.update(db, identity, "running", value)
    _guard(config, cid, identity)
    files, missing = artifact(frozen, value, source.sha256)
    checks = settings.get("policy_version", 1) == 1 or settings.get("completeness_check", True)
    value.update(
        completeness_status=("partial" if missing else "passed") if checks else "not_checked",
        known_gaps=missing,
        save_basis="checked_system_policy" if checks else "explicit_user_policy",
    )
    files["coverage.json"] = canonical(
        {
            "schema": "fiber-video-coverage/v1",
            "source_sha256": source.sha256,
            "duration_ms": frozen["duration_ms"],
            **value,
        }
    ).encode()
    folder = contained(config.state_root, "video-artifacts/" + identity)
    make_directory(folder)
    value["artifact_files"] = {}
    for name, raw in files.items():
        path = contained(folder, name)
        make_directory(path.parent)
        if path.exists():
            if read(path, 8 * 1024 * 1024) != raw:
                raise InvalidInput("video_artifact_changed")
        else:
            write_private(path, raw)
        value["artifact_files"][name] = digest(raw)
    with database.locked(config), database.connect(config.state_root) as db:
        row = video_tasks.get(config, db, cid, identity)
        if row["state"] != "running":
            raise InvalidInput("video_task_stopped")
        video_tasks.update(db, identity, "needs_confirmation", value)
        if automatic_policy.direct(settings) and (not checks or not missing):
            publish(config, db, row, value)
        return video_tasks.project(video_tasks.get(config, db, cid, identity))


def artifact(
    frozen: dict[str, Any], value: dict[str, Any], source_sha: str
) -> tuple[dict[str, bytes], list[str]]:
    cues: list[dict[str, Any]] = []
    frames: list[str] = []
    gaps: list[str] = []
    analyses: list[str] = []
    files: dict[str, bytes] = {}
    for number, segment in enumerate(value["segments"]):
        data = segment["result"]
        cues.extend(
            {
                **c,
                "start_ms": c["start_ms"] + segment["start_ms"],
                "end_ms": c["end_ms"] + segment["start_ms"],
            }
            for c in data["cues"]
        )
        if segment.get("analysis"):
            analyses.append(segment["analysis"]["summary"])
            gaps.extend(segment["analysis"]["gaps"])
        for frame in data["frames"]:
            name = f"frames/{number:04}-{frame['name']}"
            files[name] = base64.b64decode(frame["data"], validate=True)
            frames.append(f"![{segment['start_ms'] + frame['timestamp_ms']}ms]({name})")
    if not cues:
        raise InvalidInput("video_transcription_empty")
    from .media_subtitles import gaps as uncovered

    gaps.extend(
        f"transcript_gap:{g['start_ms']}-{g['end_ms']}ms"
        for g in uncovered(cues, frozen["duration_ms"])
    )
    files["note.md"] = (
        "# 视频提取\n\nsource_sha256: "
        + source_sha
        + "\n\nknowledge_verified: false\n\n"
        + "字幕/转写为来源记录；关键帧为抽样，未做人工音画核对。\n\n"
        + "\n\n".join(f"[{c['start_ms']}–{c['end_ms']} ms] {c['text']}" for c in cues)
        + "\n\n"
        + "\n\n".join(frames)
    ).encode()
    files["transcript.srt"] = normalized(cues)
    if analyses:
        files["analysis.md"] = (
            "# Pi + Skill 视频分析\n\nknowledge_verified: false\n\n" + "\n\n".join(analyses)
        ).encode()
        from .publication_note_roles import analysis_manifest

        files["publication-notes.json"] = analysis_manifest()
    return files, sorted(set(gaps))


def publish(
    config: Config,
    db: sqlite3.Connection,
    row: sqlite3.Row,
    value: dict[str, Any],
    *,
    user_confirmed: bool = False,
) -> None:
    video_tasks.get(config, db, row["client_id"], row["id"])
    files = {
        name: read(
            contained(config.state_root, "video-artifacts/" + row["id"] + "/" + name),
            8 * 1024 * 1024,
        )
        for name in value["artifact_files"]
    }
    if not files.get("note.md", b"").strip() or any(
        digest(raw) != value["artifact_files"][n] for n, raw in files.items()
    ):
        raise InvalidInput("video_artifact_changed")
    location = publication_layout.prepare(config, db, "intake", row["id"], row["vault_id"], files)
    if location is None:
        raise InvalidInput("video_unified_save_rules_required")
    if not location["plan"].get("intake_source"):
        _, version = video_tasks.source(config, db, row)
        location["plan"]["intake_source"] = {
            "source_version_id": version,
            "review_type": "user_save" if user_confirmed else "system_policy",
            "review_actor": "local_admin" if user_confirmed else "explicit_user_policy",
            "processing_kind": "video",
            "completeness_status": value["completeness_status"],
            "known_gaps": value["known_gaps"],
            "save_basis": "explicit_user_confirmation" if user_confirmed else value["save_basis"],
            "analysis_sha256": value["artifact_files"].get("analysis.md"),
        }
        db.execute(
            "UPDATE publication_locations SET value=? WHERE kind='intake' AND publication_id=?",
            (canonical(location["plan"]), row["id"]),
        )
    value["publication"] = publication_layout.publish(
        config, db, "intake", row["id"], row["client_id"], files
    )
    video_tasks.update(db, row["id"], "published", value)
