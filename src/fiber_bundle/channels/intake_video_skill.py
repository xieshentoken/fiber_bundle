"""Durable V3 extraction for channel video links; publication stays with Fiber."""

import json
from collections.abc import Callable
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .. import (
    automatic_policy,
    database,
    pi_processing,
    video_asr_api,
    video_evidence_notes,
    video_skill,
    video_skill_model,
    video_skill_notes,
    video_token_budget,
    video_visual,
)
from ..config import Config, InvalidInput, contained
from ..execution_config import ExecutionConfig
from ..inputs import canonical
from ..objects import digest
from . import intake_repository, store
from .intake_fetch import Content


def new_snapshot(
    config: Config, binding: dict[str, Any], url: str, model_sha: str, job_id: str
) -> dict[str, Any]:
    frozen = {
        "skill": video_skill.freeze(config),
        "binding": binding,
        "url": url,
        "model_sha256": model_sha,
        "completed": [],
        "job_id": job_id,
        "note_authoring": video_evidence_notes.CAPTURED_MODE,
        "evidence_flow": 2,
    }
    video_token_budget.configure(frozen)
    return frozen


def acquire(
    config: Config, identity: str, batch: str, index: int, guard: Callable[[], None]
) -> Content:
    from .intake_extraction import update

    entry, _ = intake_repository.channel(config, identity)
    client = entry["value"]["client_id"]
    with (
        database.locked(config),
        database.connect(config.state_root) as db,
        store.readonly(config) as side,
    ):
        if side is None:
            raise InvalidInput("intake_originals_required")
        row = side.execute(
            "SELECT value FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
            (identity, batch),
        ).fetchone()
        value = json.loads(row[0])
        vault = value["target_vault_id"]
        link = value["links"][index]
        binding = automatic_policy.binding(config, db, vault, client, "intake")
        if binding is None:
            raise InvalidInput("video_skill_processing_policy_required")
        model = automatic_policy.processing_route(config, binding["settings"])
        frozen = link.get("video_processing") or new_snapshot(
            config,
            binding,
            link["original_url"],
            model.digest,
            str(uuid5(NAMESPACE_URL, f"fiber-video-v3:{identity}:{batch}:{index}")),
        )
        if not link.get("video_processing") and link.get("pending_video_call_allowance"):
            frozen["call_allowance"] = link["pending_video_call_allowance"]
        if not link.get("video_processing") and link.get("pending_video_token_budget"):
            frozen["token_budget"] = link["pending_video_token_budget"]
        video_token_budget.configure(frozen)
        if frozen["binding"] != binding or frozen["model_sha256"] != model.digest:
            raise InvalidInput("video_skill_configuration_changed")
        vision_model: ExecutionConfig
        vision_reference = frozen["skill"]["settings"].get("vision_model_configuration_id")
        if vision_reference:
            from ..model_configuration.policy import resolve

            vision_model = resolve(config, vision_reference)
        else:
            vision_model = model
        if frozen.setdefault("vision_model_sha256", vision_model.digest) != vision_model.digest:
            raise InvalidInput("video_skill_configuration_changed")
        frozen["models"] = {"text": model.model, "vision": vision_model.model}
        version = value["receipts"][0]["source_version_id"]
    job = contained(config.state_root, "video-skill-jobs/" + frozen["job_id"])

    def save() -> None:
        guard()
        update(config, identity, batch, index, {"video_processing": frozen})

    def run(name: str, args: list[str], timeout: int | None = None) -> None:
        frozen["phase"] = name
        save()
        video_skill.run(config, frozen["skill"], job, name, args, guard, timeout=timeout)

    def once(stage: str, name: str, args: list[str], timeout: int | None = None) -> None:
        if stage in frozen["completed"]:
            return
        run(name, args, timeout)
        frozen["completed"].append(stage)
        save()

    save()
    if "initialize" not in frozen["completed"] and not frozen.get("source_resolution"):
        from ..video_source_resolution import resolve as resolve_source

        frozen["phase"] = "resolve_source"
        save()
        frozen["source_resolution"] = resolve_source(frozen["url"], guard)
        save()
    source_url = frozen.get("source_resolution", {}).get("final_url", frozen["url"])
    once("initialize", "init_job", [source_url, "--out", str(job)], 30)
    if "terms" not in frozen["completed"]:
        video_skill.adopt_terms(config, frozen["skill"], job, guard)
        frozen["completed"].append("terms")
        save()
    once("public_metadata", "fiber_public_probe", [], 180)
    once("media", "acquire_public", [str(job), "--mode", "media"], 1800)
    coverage = json.loads((job / "metadata/source_coverage.json").read_bytes())
    if not coverage.get("complete"):
        raise InvalidInput("video_skill_source_incomplete")
    media = list((job / "source").glob("video.*"))
    if len(media) != 1 or not media[0].is_file():
        raise InvalidInput("video_skill_source_missing")
    media_sha = video_skill.media_digest(media[0])
    if frozen.setdefault("source_media_sha256", media_sha) != media_sha:
        raise InvalidInput("video_skill_source_changed")
    save()
    if "transcript" not in frozen["completed"]:
        if "subtitle_attempt" not in frozen["completed"]:
            try:
                run("acquire_public", [str(job), "--mode", "subtitles"], 180)
            except InvalidInput:
                guard()  # Cancellation and policy changes must not become ASR fallback.
            frozen["completed"].append("subtitle_attempt")
            save()
        subtitle = job / "transcript/subtitle_selection.json"
        selection = json.loads(subtitle.read_bytes()) if subtitle.exists() else {}
        usable = False
        for relative in selection.get("files", []):
            try:
                run(
                    "normalize_transcript",
                    [str(job), "--input", str(contained(job, relative)), "--kind", "subtitle"],
                    60,
                )
                from ..media_subtitles import parse

                transcript = (job / "transcript/subtitle_normalized.srt").read_bytes()
                duration = json.loads((job / "metadata/source_metadata.json").read_bytes())[
                    "duration"
                ]
                usable = bool(parse(transcript, "srt", round(duration * 1000), long_video=True))
                if usable:
                    break
            except (InvalidInput, OSError, ValueError):
                guard()
        if usable:
            frozen["transcript_route"] = "platform_subtitle"
        else:
            # Remove only this job's unusable derived subtitle; retain original captions.
            (job / "transcript/subtitle_normalized.srt").unlink(missing_ok=True)
            run("audit_job", [str(job), "--stage", "ready-for-asr"], 30)
            try:
                if frozen["skill"]["settings"].get("asr_backend") == "transcription-api":
                    frozen["phase"] = "asr_api"
                    save()
                    video_asr_api.transcribe(config, frozen, job, save, guard)
                    frozen["transcript_route"] = "transcription_api"
                else:
                    run(
                        "run_asr",
                        [
                            str(job),
                            "--media",
                            str(media[0]),
                            "--language",
                            frozen["skill"]["settings"].get("asr_language", "auto"),
                        ],
                    )
                    frozen["transcript_route"] = "local_qwen3_asr"
            except InvalidInput as error:
                if str(error) != "video_skill_asr_empty":
                    raise
                guard()
                run("fiber_visual", ["qualify"], 60)
                frozen["visual_fallback"] = json.loads(
                    (job / "metadata/fiber_visual_route.json").read_bytes()
                )
                frozen["transcript_route"] = "visual_primary"
        frozen["completed"].append("transcript")
        save()
    visual_primary = frozen["transcript_route"] == "visual_primary"
    if visual_primary:
        run("fiber_visual", ["verify"], 60)
    once(
        "frames",
        "select_keyframes",
        video_visual.frame_arguments(job, media[0])
        if visual_primary
        else [str(job), "--video", str(media[0]), "--profile", "balanced", "--max-width", "640"],
        1800,
    )
    run_policy = pi_processing.execution_policy(model, video_authoring=True)
    frozen["phase"] = "visual_analysis"
    save()
    if (
        frozen.get("note_authoring") in video_evidence_notes.MODES
        and frozen.get("evidence_flow") != 2
    ):
        with database.locked(config), database.connect(config.state_root) as db:
            video_evidence_notes.require_cached(db, frozen, job)
    else:
        video_skill_model.visual(
            config, vision_model, frozen, job, version, client, vault, save, guard
        )
    once("crops", "crop_illustrations", [str(job)], 300)
    try:
        once(
            "timeline",
            "fiber_visual" if visual_primary else "build_evidence_timeline",
            ["timeline"] if visual_primary else [str(job)],
            60,
        )
    except InvalidInput as error:
        if visual_primary and str(error) == "video_skill_visual_content_missing":
            frozen["rejected_vision_runs"] = list(frozen.get("vision_runs", {}).values())
            save()
        raise
    if visual_primary:
        run("fiber_visual", ["ready-for-note"], 60)
    if frozen.get("note_authoring") in video_evidence_notes.MODES:
        with database.locked(config), database.connect(config.state_root) as db:
            video_evidence_notes.require_cached(db, frozen, job)
    if "note" not in frozen["completed"]:
        frozen["phase"] = (
            "evidence_note"
            if frozen.get("note_authoring") in video_evidence_notes.MODES
            else "note_authoring"
        )
        save()
        video_skill_notes.render(
            config, run_policy, frozen, job, version, client, vault, save, guard
        )
        frozen["completed"].append("note")
        save()
    if visual_primary:
        run("fiber_visual", ["final"], 60)
    else:
        run("audit_job", [str(job), "--stage", "final"], 60)
    files = video_skill_notes.export(job, frozen)
    frozen.update(
        state="complete",
        phase="complete",
        result_sha256=digest(
            canonical({name: digest(raw) for name, raw in files.items()}).encode()
        ),
    )
    save()
    source = json.loads((job / "metadata/source_metadata.json").read_bytes())
    return Content(
        {
            "kind": "video",
            "title": source.get("title"),
            "content_state": "review_required",
            "site": source.get("platform"),
            "transcript_route": frozen["transcript_route"],
            "speech_limitation": frozen.get("visual_fallback", {}).get("disclosure"),
            "video_skill_sha256": frozen["skill"]["sha256"],
            "video_job_id": frozen["job_id"],
            "audit_passed": True,
            "image_count": sum(name.endswith(".jpg") for name in files),
        },
        (),
        files,
    )


def prepare_retry(
    db: Any,
    link: dict[str, Any],
    *,
    approve_new_attempt: bool = False,
    video_note_mode: str | None = None,
    max_new_model_calls: int | None = None,
    max_video_tokens: int | None = None,
) -> None:
    from .intake_pi import prepare_retry as retry_runs

    frozen = link["video_processing"]
    rebind = (
        max_new_model_calls is not None
        or max_video_tokens is not None
        or not frozen.get("token_budget")
    )
    video_token_budget.configure(frozen, max_video_tokens)
    if frozen.get("visual_response_error"):
        frozen.setdefault("previous_visual_errors", []).append(frozen.pop("visual_response_error"))
    if max_new_model_calls is not None:
        from .. import video_call_allowance

        if frozen.get("call_allowance"):
            frozen.setdefault("previous_call_allowances", []).append(frozen["call_allowance"])
        frozen["call_allowance"] = video_call_allowance.new(max_new_model_calls)
    if video_note_mode == "direct":
        frozen.update(note_authoring=video_evidence_notes.CAPTURED_MODE, evidence_flow=2)
    selected_mode = (
        frozen.get("note_authoring")
        if video_note_mode == "direct"
        else video_note_mode or frozen.get("note_authoring")
    )
    if selected_mode in video_evidence_notes.MODES and (
        video_note_mode in video_evidence_notes.MODES or frozen.get("evidence_flow") != 2
    ):
        video_evidence_notes.require_cached(db, frozen, mode=selected_mode)
        frozen["note_authoring"] = selected_mode
        frozen.pop("evidence_flow", None)
        frozen["completed"] = [s for s in frozen["completed"] if s != "note"]
        frozen["state"] = "queued"
        return  # Failed Pi runs and their reservations remain immutable and unused.
    video_asr_api.prepare_retry(db, frozen, approve_new_attempt)
    if rebind:
        for key, rid in list(frozen.get("asr_runs", {}).items()):
            row = db.execute(
                "SELECT state FROM model_configuration_calls WHERE id=?", (rid,)
            ).fetchone()
            if row and row[0] == "queued":
                frozen.setdefault("previous_asr_runs", []).append(
                    {"run_id": rid, "chunk": key, "reason": "allowance_rebound_before_call"}
                )
                del frozen["asr_runs"][key]
    vision_before = dict(frozen.get("vision_runs", {}))
    for name in (
        ("vision_runs",)
        if frozen.get("note_authoring") in video_evidence_notes.MODES
        else ("vision_runs", "note_runs")
    ):
        part = {
            "runs": frozen.setdefault(name, {}),
            "previous_runs": frozen.setdefault("previous_" + name, []),
        }
        retry_runs(db, {"pi_processing": part}, approve_new_attempt=approve_new_attempt)
        if rebind:
            # Uncalled queued runs are retained in history and replaced so their
            # immutable binding cannot carry an older allowance into this resume.
            for key, rid in list(part["runs"].items()):
                state = db.execute("SELECT state FROM execution_runs WHERE id=?", (rid,)).fetchone()
                if (
                    state
                    and state[0] in {"queued", "waiting_budget"}
                    and not db.execute(
                        "SELECT 1 FROM model_calls WHERE run_id=?",
                        (rid,),
                    ).fetchone()
                ):
                    part["previous_runs"].append(
                        {"run_id": rid, "chunk": key, "reason": "allowance_rebound_before_call"}
                    )
                    del part["runs"][key]
        for key, rid in list(part["runs"].items()):
            if rid not in frozen.get("rejected_" + name, []):
                continue
            if (
                db.execute("SELECT 1 FROM model_calls WHERE run_id=?", (rid,)).fetchone()
                and not approve_new_attempt
            ):
                raise InvalidInput("intake_processing_new_attempt_confirmation_required")
            part["previous_runs"].append(
                {
                    "run_id": rid,
                    "chunk": key,
                    "host_result_rejected": True,
                    "new_attempt_approved": approve_new_attempt,
                }
            )
            del part["runs"][key]
    stages = {"note"}
    if frozen["vision_runs"] != vision_before:
        stages.update({"crops", "timeline"})
    frozen["completed"] = [stage for stage in frozen["completed"] if stage not in stages]
    frozen["state"] = "queued"
