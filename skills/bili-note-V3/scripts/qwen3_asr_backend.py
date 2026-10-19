#!/usr/bin/env python3
"""Run Qwen3-ASR locally and emit chunk-level timestamped JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


def value_from(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def resolve_device_map(torch: Any, requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda:0"
    mps = getattr(getattr(torch, "backends", None), "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def resolve_dtype(torch: Any, requested: str, device_map: str) -> Any:
    if requested == "auto":
        if device_map.startswith("cuda"):
            return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        return torch.float32
    mapping = {
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float16": torch.float16,
        "fp16": torch.float16,
        "float32": torch.float32,
        "fp32": torch.float32,
    }
    if requested not in mapping:
        raise ValueError(f"Unsupported dtype: {requested}")
    return mapping[requested]


def normalize_language(value: str) -> str | None:
    key = (value or "").strip().lower()
    if key in {"", "auto", "automatic", "detect"}:
        return None
    if key in {"zh", "zh-cn", "zh_cn", "cn", "chinese", "mandarin"}:
        return "Chinese"
    if key in {"en", "english"}:
        return "English"
    return value


def transcribe_one(
    model: Any, audio: Path, language: str | None, context: str
) -> dict[str, Any]:
    result = model.transcribe(audio=str(audio), language=language, context=context)
    first = result[0] if isinstance(result, list) and result else result
    return {
        "language": value_from(first, "language", language or ""),
        "text": str(value_from(first, "text", "") or "").strip(),
    }


def trim_boundary_overlap(previous: str, current: str, maximum: int = 80) -> str:
    """Remove a repeated suffix/prefix caused by overlapping ASR chunks."""
    left = " ".join(previous.split())
    right = " ".join(current.split())
    limit = min(len(left), len(right), maximum)
    for size in range(limit, 1, -1):
        if left[-size:] == right[:size]:
            return right[size:].lstrip()
    return right


def transcribe_chunks(
    model: Any,
    audio_path: Path,
    language: str | None,
    context: str,
    chunk_seconds: float,
    overlap_seconds: float,
    soundfile: Any,
    torch: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data, sample_rate = soundfile.read(str(audio_path), always_2d=False)
    total_samples = len(data)
    duration = total_samples / float(sample_rate or 1)
    if chunk_seconds <= 0 or duration <= chunk_seconds * 1.2:
        try:
            item = transcribe_one(model, audio_path, language, context)
            status = "complete"
            error = None
        except Exception as exc:  # keep a structured failure record for the caller
            item = {"language": language or "", "text": ""}
            status = "failed"
            error = str(exc)
        if status == "complete" and not item["text"]:
            status = "empty"
        item.update({"start": 0.0, "end": round(duration, 3), "chunk_index": 1})
        results = [{"chunk_index": 1, "start": 0.0, "end": round(duration, 3), "status": status}]
        if error:
            results[0]["error"] = error
        return ([item] if item["text"] else []), results

    chunk_size = max(1, int(sample_rate * chunk_seconds))
    overlap = max(0, min(overlap_seconds, chunk_seconds / 2))
    overlap_samples = int(sample_rate * overlap)
    segments: list[dict[str, Any]] = []
    chunk_results: list[dict[str, Any]] = []
    previous_text = ""
    with tempfile.TemporaryDirectory(prefix="bili_note_v2_qwen_") as directory:
        temp_dir = Path(directory)
        for chunk_index, start in enumerate(range(0, total_samples, chunk_size), start=1):
            core_end = min(start + chunk_size, total_samples)
            chunk_start = max(0, start - overlap_samples if start else 0)
            chunk_end = min(core_end + overlap_samples, total_samples)
            if chunk_end <= chunk_start:
                continue
            chunk_path = temp_dir / f"chunk_{chunk_index:04d}.wav"
            soundfile.write(str(chunk_path), data[chunk_start:chunk_end], sample_rate)
            result = {
                "chunk_index": chunk_index,
                "start": round(start / sample_rate, 3),
                "end": round(core_end / sample_rate, 3),
                "input_start": round(chunk_start / sample_rate, 3),
                "input_end": round(chunk_end / sample_rate, 3),
                "status": "complete",
            }
            try:
                item = transcribe_one(model, chunk_path, language, context)
            except Exception as exc:
                result["status"] = "failed"
                result["error"] = str(exc)
                chunk_results.append(result)
                continue
            text = trim_boundary_overlap(previous_text, item["text"])
            if text:
                item["text"] = text
                item.update(
                    {
                        "start": round(start / sample_rate, 3),
                        "end": round(core_end / sample_rate, 3),
                        "chunk_index": chunk_index,
                    }
                )
                segments.append(item)
                previous_text = text
            elif not item["text"]:
                result["status"] = "empty"
            chunk_results.append(result)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    return segments, chunk_results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--context-file", required=True, type=Path)
    parser.add_argument("--model", default="Qwen/Qwen3-ASR-0.6B")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--dtype", default="auto")
    parser.add_argument("--max-new-tokens", type=int, default=8192)
    parser.add_argument("--chunk-seconds", type=float, default=30.0)
    parser.add_argument("--chunk-overlap-seconds", type=float, default=1.5)
    args = parser.parse_args()

    try:
        import soundfile
        import torch
        from qwen_asr import Qwen3ASRModel
    except Exception as exc:
        print(f"Qwen3-ASR runtime is unavailable: {exc}", file=sys.stderr)
        return 3

    context = args.context_file.read_text(encoding="utf-8").strip()
    device_map = resolve_device_map(torch, args.device_map)
    dtype = resolve_dtype(torch, args.dtype, device_map)
    model = Qwen3ASRModel.from_pretrained(
        args.model,
        dtype=dtype,
        device_map=device_map,
        max_new_tokens=args.max_new_tokens,
        max_inference_batch_size=1,
    )
    segments, chunk_results = transcribe_chunks(
        model,
        args.audio,
        normalize_language(args.language),
        context,
        args.chunk_seconds,
        args.chunk_overlap_seconds,
        soundfile,
        torch,
    )
    text = "\n".join(item["text"] for item in segments).strip()
    if not text:
        print("Qwen3-ASR returned no transcript text", file=sys.stderr)
        return 4
    payload = {
        "backend": "qwen3-asr",
        "model": args.model,
        "language": segments[0].get("language", args.language),
        "device_map": device_map,
        "dtype": str(dtype).replace("torch.", ""),
        "chunk_seconds": args.chunk_seconds,
        "chunk_overlap_seconds": args.chunk_overlap_seconds,
        "context_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest(),
        "chunk_results": chunk_results,
        "failed_chunk_count": sum(1 for item in chunk_results if item["status"] != "complete"),
        "segments": segments,
        "text": text,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
