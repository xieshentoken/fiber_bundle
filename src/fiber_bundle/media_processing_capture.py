"""Fixed container processing and host verification of bound transcription/frame evidence."""

import base64
import hashlib
import subprocess
from pathlib import Path
from typing import Any

from .config import InvalidInput
from .media_capture import probe
from .media_files import canonical, decode
from .media_processing_contract import plan
from .media_processing_files import read, write
from .media_subtitles import gaps, normalized, parse
from .media_transcript import chunks, convert


def candidate(
    spec: dict[str, Any],
    raw: list[dict[str, Any]] | None,
    subtitle: bytes | None,
    frames: object,
) -> dict[str, Any]:
    plan(spec)
    duration = spec["duration_ms"]
    if spec["asr"]:
        if raw is None or subtitle is not None:
            raise InvalidInput("media_processing_transcript_route")
        transcript = {"provenance": "local_qwen3_asr", **convert(raw, spec["glossary"], duration)}
    else:
        if raw is not None or subtitle is None:
            raise InvalidInput("media_processing_transcript_route")
        cues = parse(subtitle, spec["request"]["subtitle_kind"], duration)
        transcript = {
            "provenance": "user_supplied_separate_asset",
            "cues": cues,
            "corrections": [],
            "ambiguities": [],
            "uncovered_ranges": gaps(cues, duration),
            "raw_sha256": hashlib.sha256(subtitle).hexdigest(),
            "normalized_sha256": hashlib.sha256(normalized(cues)).hexdigest() if cues else None,
            "timestamp_basis": "supplied_subtitle",
            "word_alignment_verified": False,
            "semantic_review_verified": False,
        }
    expected = (
        list(range(0, duration, 10000))
        if spec["request"]["evidence_mode"] == "full_evidence"
        else []
    )
    if not isinstance(frames, list) or len(frames) != len(expected):
        raise InvalidInput("media_processing_frame_count")
    for i, (f, start) in enumerate(zip(frames, expected, strict=True)):
        if (
            not isinstance(f, dict)
            or set(f) != {"name", "timestamp_ms", "sha256", "data"}
            or type(f["timestamp_ms"]) is not int
            or not isinstance(f["sha256"], str)
            or not isinstance(f["data"], str)
            or f["name"] != f"frame-{i:03}.jpg"
            or f["timestamp_ms"] != start
        ):
            raise InvalidInput("media_processing_frame_locator")
        try:
            data = base64.b64decode(f["data"], validate=True)
        except (ValueError, TypeError) as e:
            raise InvalidInput("media_processing_frame_data") from e
        if (
            not 0 < len(data) <= 65536
            or not data.startswith(b"\xff\xd8")
            or not data.endswith(b"\xff\xd9")
            or hashlib.sha256(data).hexdigest() != f["sha256"]
        ):
            raise InvalidInput("media_processing_frame_sha")
    complete = not transcript["uncovered_ranges"] and not transcript["ambiguities"]
    return {
        "schema": "fiber-media-processing-result/v1",
        "source_sha256": spec["source_sha256"],
        "plan_sha256": hashlib.sha256(canonical(spec)).hexdigest(),
        "duration_ms": duration,
        "transcript": transcript,
        "frames": frames,
        "complete": complete,
        "frame_sampling_interval_ms": 10000 if expected else None,
        "visual_review_verified": False,
        "knowledge_verified": False,
        "publication_ready": False,
        "cleanup_allowed": False,
        "model_calls": len(raw) if spec["asr"] and raw is not None else 0,
    }


def asr(source: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
    # Offline local paths only; pinned model contents checked before loading any weights.
    import json

    lock = json.loads(Path("/opt/model-lock.json").read_bytes())
    if (
        hashlib.sha256(Path("/opt/model-lock.json").read_bytes()).hexdigest()
        != spec["policy"]["model_lock_sha256"]
    ):
        raise InvalidInput("media_processing_runtime_model_lock")
    for name, item in lock["files"].items():
        path = Path("/opt/model") / name
        h = hashlib.sha256()
        if "/" in name or path.is_symlink() or path.stat().st_size != item["byte_count"]:
            raise InvalidInput("media_processing_weights")
        with path.open("rb") as f:
            while data := f.read(1024 * 1024):
                h.update(data)
        if h.hexdigest() != item["sha256"]:
            raise InvalidInput("media_processing_weights")
    import importlib

    sf = importlib.import_module("soundfile")
    torch = importlib.import_module("torch")
    Qwen3ASRModel = importlib.import_module("qwen_asr").Qwen3ASRModel

    torch.set_num_threads(spec["policy"]["cpus"])
    model = Qwen3ASRModel.from_pretrained(
        "/opt/model",
        dtype=torch.float32,
        device_map="cpu",
        max_inference_batch_size=1,
        max_new_tokens=256,
        attn_implementation="eager",
    )
    audio = Path("/tmp/source.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(audio),
        ],
        check=True,
        timeout=60,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    data, sr = sf.read(audio, dtype="float32")
    duration = spec["duration_ms"]
    result = []
    for start in range(0, duration, 15000):
        end = min(start + 15000, duration)
        value = model.transcribe(
            audio=(data[start * sr // 1000 : end * sr // 1000], sr), language=None, context=""
        )[0]
        body = value.text
        result.append(
            {
                "start_ms": start,
                "end_ms": end,
                "text": body,
                "language": value.language,
                "state": "complete" if body.strip() else "empty",
            }
        )
    return chunks(result, duration)


def extract_frames(
    source: Path, duration_ms: int, outputs: Path, *, temporary: Path = Path("/tmp")
) -> list[dict[str, Any]]:
    frames = []
    for i, start in enumerate(range(0, duration_ms, 10000)):
        name = f"frame-{i:03}.jpg"
        path = temporary / name
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "1",
                "-protocol_whitelist",
                "file",
                "-ss",
                str(start / 1000),
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-2",
                "-q:v",
                "5",
                str(path),
            ],
            check=True,
            timeout=20,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        data = path.read_bytes()
        write(outputs, name, data)
        frames.append(
            {
                "name": name,
                "timestamp_ms": start,
                "sha256": hashlib.sha256(data).hexdigest(),
                "data": base64.b64encode(data).decode(),
            }
        )
    return frames


def capture(inputs: Path, outputs: Path) -> None:
    spec = plan(decode(read(inputs, "request.json", 128 * 1024)))
    names = {"request.json", "source"} | ({"subtitle"} if not spec["asr"] else set())
    if {p.name for p in inputs.iterdir()} != names or any(outputs.iterdir()):
        raise InvalidInput("media_processing_scope")
    source = read(inputs, "source")
    if (
        len(source) != spec["source_bytes"]
        or hashlib.sha256(source).hexdigest() != spec["source_sha256"]
    ):
        raise InvalidInput("media_processing_source_binding")
    info = probe(inputs / "source", spec["request"])
    if info["duration_ms"] != spec["duration_ms"]:
        raise InvalidInput("media_processing_duration")
    raw = asr(inputs / "source", spec) if spec["asr"] else None
    subtitle = read(inputs, "subtitle", 65536) if not spec["asr"] else None
    frames = (
        extract_frames(inputs / "source", spec["duration_ms"], outputs)
        if spec["request"]["evidence_mode"] == "full_evidence"
        else []
    )
    value = candidate(spec, raw, subtitle, frames)
    if raw is not None:
        write(outputs, "raw.json", canonical({"chunks": raw}))
    if value["transcript"]["cues"]:
        write(outputs, "normalized.srt", normalized(value["transcript"]["cues"]))
    write(outputs, "result.json", canonical(value))


def verify(
    inputs: Path, outputs: Path, source: bytes, subtitle: bytes | None, spec: dict[str, Any]
) -> dict[str, Any]:
    if (
        read(inputs, "source") != source
        or read(inputs, "request.json", 128 * 1024) != canonical(spec)
        or (subtitle is not None and read(inputs, "subtitle", 65536) != subtitle)
    ):
        raise InvalidInput("media_processing_snapshot_changed")
    names = {"source", "request.json"} | ({"subtitle"} if subtitle is not None else set())
    if {p.name for p in inputs.iterdir()} != names:
        raise InvalidInput("media_processing_input_scope")
    value = decode(read(outputs, "result.json"))
    raw = decode(read(outputs, "raw.json", 256 * 1024)) if spec["asr"] else None
    if raw is not None and set(raw) != {"chunks"}:
        raise InvalidInput("media_processing_raw_fields")
    expected = candidate(spec, raw["chunks"] if raw else None, subtitle, value.get("frames"))
    if value != expected:
        raise InvalidInput("media_processing_output_binding")
    names = {"result.json"} | ({"raw.json"} if spec["asr"] else set())
    if value["transcript"]["cues"]:
        names.add("normalized.srt")
        if read(outputs, "normalized.srt", 65536) != normalized(value["transcript"]["cues"]):
            raise InvalidInput("media_processing_normalized_changed")
    for f in value["frames"]:
        names.add(f["name"])
        if read(outputs, f["name"], 65536) != base64.b64decode(f["data"]):
            raise InvalidInput("media_processing_frame_changed")
    if {p.name for p in outputs.iterdir()} != names:
        raise InvalidInput("media_processing_output_scope")
    return value
