"""V3 frame calls share the normal model ledger; Pi authors audited text evidence."""

import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import database, execution, execution_ledger, pi_processing
from .config import Config, InvalidInput, StorageError, contained, make_directory
from .inputs import canonical
from .model_budgets import Exhausted
from .objects import digest
from .query_policy_store import write_private
from .video_visual_contract import VisualContractError
from .video_visual_contract import json_reply as json_reply
from .video_visual_contract import prompt as visual_prompt
from .video_visual_contract import validate_visual as validate_visual


def vision_payload(policy: Any, text: str, image: bytes) -> dict[str, Any]:
    data = base64.b64encode(image).decode()
    protocol = getattr(policy, "protocol", "openai-completions")
    if protocol == "anthropic-messages":
        blocks = [
            {"type": "text", "text": text},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
            },
        ]
    elif protocol == "openai-responses":
        return {
            "_fiber_native": protocol,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": text},
                        {"type": "input_image", "image_url": "data:image/jpeg;base64," + data},
                    ],
                }
            ],
        }
    else:
        blocks = [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + data}},
        ]
    return {"_fiber_native": protocol, "messages": [{"role": "user", "content": blocks}]}


def visual(
    config: Config,
    policy: Any,
    frozen: dict[str, Any],
    job: Path,
    version: str,
    client: str,
    vault: str,
    save: Callable[[], None],
    guard: Callable[[], None],
) -> None:
    frames = json.loads((job / "frames/frame_manifest.json").read_bytes()).get("frames", [])
    if not frames:
        raise InvalidInput("video_skill_frames_missing")
    rows = []
    for frame in frames:
        guard()
        fid = frame["frame_id"]
        raw = contained(job, frame["selected_path"]).read_bytes()
        if digest(raw) != frame["sha256"] or len(raw) > policy.max_request_bytes // 2:
            raise InvalidInput("video_skill_frame_invalid")
        reply_path = contained(job, "visual/" + fid + ".json")
        runs = frozen.setdefault("vision_runs", {})
        rid = runs.get(fid)
        if rid is None:
            with database.locked(config), database.connect(config.state_root) as db:
                rid = pi_processing.prepare(
                    config,
                    db,
                    policy,
                    client,
                    vault,
                    version,
                    "video",
                    frame["sha256"],
                    [{"id": fid, "text": "V3 selected-frame visual analysis"}],
                    allow_cloud=True,
                )
            runs[fid] = rid
            save()
        if reply_path.exists():
            previous = json.loads(reply_path.read_bytes())
            if previous.get("run_id") != rid:
                if not any(
                    p.get("run_id") == previous.get("run_id") and p.get("chunk") == fid
                    for p in frozen.get("previous_vision_runs", [])
                ):
                    raise InvalidInput("video_skill_visual_result_changed")
                guard()
                archive = contained(
                    job, "visual/previous/" + previous["run_id"] + "-" + fid + ".json"
                )
                make_directory(archive.parent)
                write_private(archive, reply_path.read_bytes())
                reply_path.unlink()
        with database.locked(config), database.connect(config.state_root) as db:
            state = execution_ledger.get_run(db, rid)["state"]
            if reply_path.exists():
                row = json.loads(reply_path.read_bytes())
                if (
                    row.get("run_id") != rid
                    or row["visual_verification"]["input_sha256"] != frame["sha256"]
                ):
                    raise InvalidInput("video_skill_visual_result_changed")
                if state == "running":
                    execution_ledger.transition(db, rid, "succeeded")
                elif state != "succeeded":
                    raise InvalidInput("video_skill_model_reconciliation_required")
                rows.append(row)
                continue
            if state not in {"queued", "waiting_budget"}:
                raise InvalidInput("video_skill_model_reconciliation_required")
            execution_ledger.start(config, db, rid, policy)
        prompt = visual_prompt(visual_primary=frozen.get("transcript_route") == "visual_primary")
        try:
            reply = execution.handle(
                config,
                policy,
                rid,
                {"type": "model", "id": fid, "body": vision_payload(policy, prompt, raw)},
            )
            make_directory(job / "visual/responses")
            write_private(
                job / "visual/responses" / (rid + "-" + fid + ".json"),
                canonical(
                    {
                        "run_id": rid,
                        "frame_id": fid,
                        "input_sha256": frame["sha256"],
                        "body": reply["body"],
                    }
                ).encode(),
            )
            guard()
            row = json_reply(reply["body"])
            validate_visual(row, rows)
            if frozen.get("transcript_route") == "visual_primary" and (
                any(
                    not isinstance(row.get(k), list) or any(not isinstance(x, str) for x in row[k])
                    for k in ("ocr_text", "claims")
                )
                or row["analysis_status"] == "not_needed"
                and row["illustrations"]
            ):
                raise InvalidInput("video_skill_visual_response_invalid")
            row.update(
                frame_id=fid,
                timestamp=frame["timestamp"],
                run_id=rid,
                cloud_provider=getattr(policy, "provider", "configured"),
                cloud_model=policy.model,
                visual_verification={
                    "verified": True,
                    "method": "cloud_vision",
                    "model": policy.model,
                    "input_sha256": frame["sha256"],
                },
            )
            write_private(reply_path, canonical(row).encode())
            guard()
            with database.locked(config), database.connect(config.state_root) as db:
                execution_ledger.transition(db, rid, "succeeded")
            rows.append(row)
        except Exception as error:
            with database.locked(config), database.connect(config.state_root) as db:
                execution_ledger.transition(
                    db,
                    rid,
                    "waiting_budget" if isinstance(error, Exhausted) else "failed",
                    "model_key_budget_exhausted"
                    if isinstance(error, Exhausted)
                    else getattr(error, "code", "video_skill_visual_failed"),
                )
            if isinstance(error, VisualContractError):
                frozen["visual_response_error"] = {
                    "frame_id": fid,
                    "run_id": rid,
                    "field": error.field,
                    "response_path": "visual/responses/" + rid + "-" + fid + ".json",
                }
                save()
            raise
    frozen.pop("visual_response_error", None)
    write_private(
        job / "visual/visual_results.jsonl", ("\n".join(canonical(r) for r in rows) + "\n").encode()
    )


def author(
    config: Config,
    policy: Any,
    frozen: dict[str, Any],
    evidence: dict[str, Any],
    version: str,
    client: str,
    vault: str,
    save: Callable[[], None],
    guard: Callable[[], None],
) -> str:
    guard()
    key = evidence["segment_id"] + ":" + digest(canonical(evidence).encode())[:12]
    runs = frozen.setdefault("note_runs", {})
    rid = runs.get(key)
    if rid is None:
        with database.locked(config), database.connect(config.state_root) as db:
            rid = pi_processing.prepare(
                config,
                db,
                policy,
                client,
                vault,
                version,
                "video",
                digest(canonical(evidence).encode()),
                [{"id": key, "text": canonical(evidence)}],
                allow_cloud=True,
                video_skill=frozen["skill"],
            )
        runs[key] = rid
        save()
    with database.locked(config), database.connect(config.state_root) as db:
        state = execution_ledger.get_run(db, rid)["state"]
    if state in {"queued", "waiting_budget"}:
        execution.execute(config, policy, rid, "fixed video V3 authoring skill")
    guard()
    rejected = False
    with database.locked(config), database.connect(config.state_root) as db:
        if execution_ledger.get_run(db, rid)["state"] != "succeeded":
            raise InvalidInput("video_skill_note_requires_recovery")
        try:
            result = pi_processing.result(config, db, rid)
        except (StorageError, ValueError):
            rejected = True
    if rejected:
        if rid not in frozen.setdefault("rejected_note_runs", []):
            frozen["rejected_note_runs"].append(rid)
            save()
        raise InvalidInput("video_skill_note_requires_recovery")
    return str(result["note_markdown"])
