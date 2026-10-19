#!/usr/bin/env python3
"""Crop standalone charts, tables, diagrams, and other useful visual regions."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Any

from common import is_audio_only_job, read_json, read_jsonl, update_pipeline_state, write_json
from audit_visual_evidence import validate_visual_rows


ALLOWED_KINDS = {
    "chart",
    "table",
    "diagram",
    "data_visualization",
    "formula",
    "code",
    "ui_detail",
    "physical_detail",
}


def validate_bbox(value: Any, min_area: float = 0.005, max_area: float = 0.85) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("bbox_norm must be [x_min, y_min, x_max, y_max]")
    try:
        x_min, y_min, x_max, y_max = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox_norm values must be numeric") from exc
    if not (0.0 <= x_min < x_max <= 1.0 and 0.0 <= y_min < y_max <= 1.0):
        raise ValueError("bbox_norm coordinates must be ordered within 0..1")
    area = (x_max - x_min) * (y_max - y_min)
    if area < min_area:
        raise ValueError(f"crop area {area:.3f} is too small")
    if area > max_area:
        raise ValueError(
            f"crop area {area:.3f} looks like a full-frame screenshot; refine the region"
        )
    return [x_min, y_min, x_max, y_max]


def padded_bbox(bbox: list[float], padding: float) -> list[float]:
    x_min, y_min, x_max, y_max = bbox
    return [
        max(0.0, x_min - padding),
        max(0.0, y_min - padding),
        min(1.0, x_max + padding),
        min(1.0, y_max + padding),
    ]


def build_crop_filter(bbox: list[float]) -> str:
    x_min, y_min, x_max, y_max = bbox
    return (
        f"crop=iw*{x_max - x_min:.8f}:ih*{y_max - y_min:.8f}:"
        f"iw*{x_min:.8f}:ih*{y_min:.8f}"
    )


def crop_image(source: Path, destination: Path, bbox: list[float]) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to crop note illustrations")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            build_crop_filter(bbox),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(destination),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1600:] or "ffmpeg crop failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--padding", type=float, default=0.015)
    parser.add_argument("--max-area", type=float, default=0.85)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not 0.0 <= args.padding <= 0.1:
        parser.error("--padding must be between 0 and 0.1")

    job_dir = args.job.expanduser().resolve()
    if is_audio_only_job(read_json(job_dir / "metadata" / "job.json")):
        parser.error("Audio-only evidence mode does not permit illustration cropping")
    visual_path = job_dir / "visual" / "visual_results.jsonl"
    visual_rows = list(read_jsonl(visual_path)) if visual_path.exists() else []
    frame_by_id, visual_rows, visual_errors = validate_visual_rows(
        job_dir,
        visual_rows=visual_rows,
    )
    if visual_errors:
        parser.error("Visual evidence audit failed: " + "; ".join(visual_errors))
    output_dir = job_dir / "illustrations"
    output_dir.mkdir(parents=True, exist_ok=True)

    items: list[dict[str, Any]] = []
    failures = 0
    illustration_index = 0
    for visual in sorted(
        visual_rows,
        key=lambda row: float(frame_by_id[str(row["frame_id"])] ["timestamp"]),
    ):
        if visual.get("analysis_status") != "analyzed":
            continue
        frame_id = str(visual.get("frame_id"))
        frame = frame_by_id.get(frame_id)
        for region_index, region in enumerate(visual.get("illustrations", []), start=1):
            if region.get("recommended", True) is not True:
                continue
            illustration_index += 1
            illustration_id = f"I{illustration_index:04d}"
            kind = str(region.get("kind") or "")
            item: dict[str, Any] = {
                "illustration_id": illustration_id,
                "frame_id": frame_id,
                # The manifest is the only timestamp authority.  Keeping this
                # assignment explicit prevents a stale/model-written timestamp
                # from leaking into crops, evidence, or the final note.
                "timestamp": frame.get("timestamp") if frame else None,
                "region_id": str(region.get("region_id") or f"R{region_index:02d}"),
                "kind": kind,
                "caption": str(region.get("caption") or "").strip(),
                "status": "failed",
            }
            try:
                if frame is None:
                    raise ValueError(f"frame {frame_id} is missing from frame_manifest.json")
                if kind not in ALLOWED_KINDS:
                    raise ValueError(f"unsupported illustration kind: {kind}")
                if not item["caption"]:
                    raise ValueError("illustration caption is required")
                bbox = validate_bbox(region.get("bbox_norm"), max_area=args.max_area)
                crop_bbox = padded_bbox(bbox, args.padding)
                if (crop_bbox[2] - crop_bbox[0]) * (crop_bbox[3] - crop_bbox[1]) > args.max_area:
                    crop_bbox = bbox
                source_relative = Path(str(frame.get("selected_path") or ""))
                source = (job_dir / source_relative).resolve()
                if not source.is_relative_to(job_dir) or not source.is_file():
                    raise ValueError(f"selected frame is missing or outside the job: {source_relative}")
                safe_kind = kind.replace("_", "-")
                destination = output_dir / f"{illustration_id}_{frame_id}_{safe_kind}.jpg"
                if args.force or not destination.exists():
                    crop_image(source, destination, crop_bbox)
                item.update(
                    {
                        "status": "created",
                        "bbox_norm": bbox,
                        "crop_bbox_norm": crop_bbox,
                        "source_path": source_relative.as_posix(),
                        "path": destination.relative_to(job_dir).as_posix(),
                        "size": destination.stat().st_size,
                    }
                )
            except (ValueError, RuntimeError) as exc:
                failures += 1
                item["error"] = str(exc)
            items.append(item)

    created = [item for item in items if item["status"] == "created"]
    manifest = {
        "schema_version": "1.0",
        "policy": "cropped_regions_only",
        "full_frame_fallback": False,
        "candidate_count": len(items),
        "created_count": len(created),
        "failed_count": failures,
        "items": items,
    }
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    update_pipeline_state(
        job_dir,
        "illustrations",
        "complete" if failures == 0 else "needs_review",
        f"{len(created)}/{len(items)} recommended regions cropped",
    )
    print(manifest_path)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
