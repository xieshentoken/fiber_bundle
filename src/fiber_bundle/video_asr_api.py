"""Durable chunked API ASR; successful chunks and unknown charges are never replayed."""

import json
import time
import wave
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import database, model_budgets, video_asr_transport, video_skill
from .config import Config, InvalidInput, contained, make_directory
from .inputs import canonical
from .model_configuration.policy import RegisteredPolicy, resolve
from .objects import digest
from .query_policy_store import write_private


def selected_policy(config: Config, settings: dict[str, Any]) -> RegisteredPolicy:
    policy = resolve(config, settings["asr_api_model_configuration_id"])
    video_asr_transport.endpoint(policy)
    return policy


def attempt(
    config: Config,
    policy: RegisteredPolicy,
    frozen: dict[str, Any],
    job: Path,
    key: str,
    audio: bytes,
    prompt: str,
    save: Callable[[], None],
    guard: Callable[[], None],
) -> dict[str, Any]:
    settings = frozen["skill"]["settings"]
    runs = frozen.setdefault("asr_runs", {})
    rid = runs.get(key)
    if rid is None:
        guard()
        rid = str(uuid4())
        with (
            database.locked(config),
            database.connect(config.state_root) as db,
            database.transaction(db),
        ):
            reservation = model_budgets.reserve(
                db, policy.secret_file, settings.get("asr_api_reserve_tokens", 16384), 0
            )
            if reservation is None:
                raise InvalidInput("video_asr_budget_required")
            db.execute(
                "INSERT INTO model_configuration_calls "
                "VALUES(?,?,?,?,?,'queued',?,?,NULL,NULL,NULL,NULL,?)",
                (
                    rid,
                    policy.configuration_id,
                    model_budgets.key(db, policy.secret_file),
                    policy.provider,
                    policy.model,
                    canonical(
                        {
                            **asdict(policy),
                            "purpose": "video_asr",
                            "job_id": frozen["job_id"],
                            "chunk": key,
                        }
                    ),
                    reservation,
                    time.time(),
                ),
            )
        runs[key] = rid
        save()
    response_path = contained(job, "transcript/api-results/" + rid + ".json")
    with database.locked(config), database.connect(config.state_root) as db:
        row = db.execute("SELECT * FROM model_configuration_calls WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise InvalidInput("video_asr_reconciliation_required")
        if not response_path.exists():
            if row["state"] != "queued":
                raise InvalidInput("video_asr_reconciliation_required")
            db.execute("UPDATE model_configuration_calls SET state='running' WHERE id=?", (rid,))
    try:
        if response_path.exists():
            envelope = json.loads(response_path.read_bytes())
            if (
                envelope["run_id"] != rid
                or envelope["audio_sha256"] != digest(audio)
                or envelope["policy_sha256"] != policy.digest
            ):
                raise InvalidInput("video_asr_result_changed")
            reply = envelope["result"]
        else:
            guard()
            reply = video_asr_transport.transcribe(
                policy,
                audio,
                prompt,
                settings.get("asr_language", "auto"),
                settings.get("asr_api_response_format", "json"),
            )
            envelope = {
                "run_id": rid,
                "audio_sha256": digest(audio),
                "policy_sha256": policy.digest,
                "result": reply,
            }
            make_directory(response_path.parent)
            write_private(response_path, canonical(envelope).encode())
        response_sha = video_skill.media_digest(response_path)
        checksums = frozen.setdefault("asr_response_sha256", {})
        if checksums.setdefault(rid, response_sha) != response_sha:
            raise InvalidInput("video_asr_result_changed")
        usage = video_asr_transport.tokens(reply.get("usage"))
        with (
            database.locked(config),
            database.connect(config.state_root) as db,
            database.transaction(db),
        ):
            if usage:
                model_budgets.settle(db, row["reservation_id"], sum(usage), 0)
            else:
                db.execute(
                    "UPDATE model_budget_reservations SET state='uncertain' "
                    "WHERE id=? AND state!='settled'",
                    (row["reservation_id"],),
                )
            db.execute(
                "UPDATE model_configuration_calls SET state='succeeded',input_tokens=?,"
                "output_tokens=?,error_code=NULL WHERE id=?",
                (*(usage or (None, None)), rid),
            )
    except Exception as error:
        with database.locked(config), database.connect(config.state_root) as db:
            db.execute(
                "UPDATE model_configuration_calls SET state='failed',error_code=? "
                "WHERE id=? AND state!='succeeded'",
                (getattr(error, "code", "video_asr_api_failed"), rid),
            )
            db.execute(
                "UPDATE model_budget_reservations SET state='uncertain' "
                "WHERE id=? AND state='reserved'",
                (row["reservation_id"],),
            )
        raise
    guard()
    save()
    return dict(reply)


def transcribe(
    config: Config,
    frozen: dict[str, Any],
    job: Path,
    save: Callable[[], None],
    guard: Callable[[], None],
) -> None:
    settings = frozen["skill"]["settings"]
    policy = selected_policy(config, settings)
    if frozen.setdefault("asr_policy_sha256", policy.digest) != policy.digest:
        raise InvalidInput("video_skill_configuration_changed")
    save()
    video_skill.run(config, frozen["skill"], job, "fiber_asr_api", ["prepare"], guard, timeout=300)
    audio = job / "transcript/asr_audio.wav"
    prompt = (job / "transcript/asr_prompt.txt").read_text()
    segments, chunks = [], []
    with wave.open(str(audio)) as stream:
        count, rate = stream.getnframes(), stream.getframerate()
    if count <= 0 or rate != 16000:
        raise InvalidInput("video_asr_audio_invalid")
    started = time.monotonic()
    for index, start in enumerate(range(0, count, rate * 30), 1):
        guard()
        if time.monotonic() - started > settings["timeout_seconds"]:
            raise InvalidInput("video_skill_run_asr_timeout")
        end = min(count, start + rate * 30)
        raw = video_asr_transport.audio_chunk(audio, start, end - start)
        reply = attempt(config, policy, frozen, job, str(index), raw, prompt, save, guard)
        row = {
            "chunk_index": index,
            "start": start / rate,
            "end": end / rate,
            "status": "complete" if reply["text"] else "empty",
        }
        chunks.append(row)
        if reply["text"]:
            segments.append(
                {**row, "text": reply["text"], "language": settings.get("asr_language", "auto")}
            )
    source = json.loads((job / "metadata/source_coverage.json").read_bytes())
    diagnostic = {
        "schema": "fiber-api-asr-diagnostic/v1",
        "source_media_sha256": video_skill.media_digest(contained(job, source["media_path"])),
        "audio_sha256": video_skill.media_digest(audio),
        "context_sha256": video_skill.media_digest(job / "transcript/asr_prompt.txt"),
        "backend_sha256": video_skill.media_digest(Path(__file__)),
        "transcript_segments": len(segments),
        "transcript_characters": sum(len(s["text"]) for s in segments),
        "chunks": chunks,
        "policy_sha256": policy.digest,
        "response_files": {
            rid: video_skill.media_digest(job / "transcript/api-results" / (rid + ".json"))
            for rid in frozen["asr_runs"].values()
        },
    }
    write_private(job / "transcript/asr_diagnostic.json", canonical(diagnostic).encode())
    write_private(
        job / "transcript/asr_backend_raw.json",
        canonical(
            {
                "backend": "transcription-api",
                "model": policy.model,
                "segments": segments,
                "chunk_results": chunks,
            }
        ).encode(),
    )
    if not segments:
        raise InvalidInput("video_skill_asr_empty")
    video_skill.run(config, frozen["skill"], job, "fiber_asr_api", ["finish"], guard, timeout=60)


def has_result(db: Any, frozen: dict[str, Any], rid: str) -> bool:
    location = next((r[2] for r in db.execute("PRAGMA database_list") if r[1] == "main"), "")
    if not location:
        return False
    path = contained(
        Path(location).parent,
        "video-skill-jobs/" + frozen["job_id"] + "/transcript/api-results/" + rid + ".json",
    )
    return path.is_file()


def prepare_retry(db: Any, frozen: dict[str, Any], approve: bool) -> None:
    runs = frozen.setdefault("asr_runs", {})
    for chunk, rid in list(runs.items()):
        row = db.execute(
            "SELECT state FROM model_configuration_calls WHERE id=?", (rid,)
        ).fetchone()
        if (
            row
            and row["state"] in {"failed", "interrupted", "running"}
            and not has_result(db, frozen, rid)
        ):
            if not approve:
                raise InvalidInput("processing_model_new_attempt_confirmation_required")
            frozen.setdefault("previous_asr_runs", []).append({"chunk": chunk, "run_id": rid})
            del runs[chunk]
