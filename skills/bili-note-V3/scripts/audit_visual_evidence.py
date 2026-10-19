#!/usr/bin/env python3
"""Audit visual evidence provenance, timestamps, and note illustration bindings.

The frame manifest is the single source of truth for timestamps.  This audit is
deliberately deterministic: it cannot prove that a model's caption is true,
but it can prove that a caption/illustration was produced from the frame and
timepoint it claims, by an explicitly verified visual-analysis method.
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Any, Iterable

from common import read_json, read_jsonl, utc_now, write_json


TIMESTAMP_TOLERANCE_SECONDS = 0.1
ANALYSIS_STATUSES = {"analyzed", "not_needed", "failed"}
VERIFIED_METHODS = {"cloud_vision", "local_vlm", "human"}
SKIP_REASON_CODES = {"duplicate", "transition", "talking_head", "no_unique_visual"}
MANUAL_PROVIDERS = {"manual", "manual-analysis", "manual_analysis"}
FRAME_ID_RE = re.compile(r"\bF\d{4,}\b")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((<[^>]+>|[^\)]+)\)")


def _check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return number


def _relative_path(job_dir: Path, value: Any, label: str) -> Path:
    text = str(value or "")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be a safe relative path: {text!r}")
    resolved = (job_dir / path).resolve()
    if not resolved.is_relative_to(job_dir):
        raise ValueError(f"{label} escapes the job: {text!r}")
    return path


def load_frame_manifest(job_dir: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Return the selected frame map and structural errors."""
    path = job_dir / "frames" / "frame_manifest.json"
    if not path.exists():
        return {}, [f"missing frame manifest: {path}"]
    try:
        payload = read_json(path)
    except (OSError, ValueError) as exc:
        return {}, [f"cannot read frame manifest: {exc}"]
    rows = payload.get("frames")
    if not isinstance(rows, list):
        return {}, ["frame manifest frames must be a list"]
    frames: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"frame manifest row {index} is not an object")
            continue
        frame_id = str(row.get("frame_id") or "")
        if not frame_id:
            errors.append(f"frame manifest row {index} has no frame_id")
            continue
        if frame_id in frames:
            errors.append(f"duplicate frame_id in manifest: {frame_id}")
            continue
        try:
            timestamp = _number(row.get("timestamp"), f"{frame_id}.timestamp")
            selected_path = _relative_path(
                job_dir, row.get("selected_path"), f"{frame_id}.selected_path"
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        frame = dict(row)
        frame["frame_id"] = frame_id
        frame["timestamp"] = timestamp
        frame["selected_path"] = selected_path.as_posix()
        frames[frame_id] = frame
    return frames, errors


def _verification_errors(
    row: dict[str, Any],
    frame: dict[str, Any],
    *,
    frame_id: str,
) -> list[str]:
    errors: list[str] = []
    verification = row.get("visual_verification")
    if not isinstance(verification, dict):
        # ``analysis_provenance`` is accepted as a transitional spelling, but
        # new writers should use visual_verification.
        verification = row.get("analysis_provenance")
    if not isinstance(verification, dict) or verification.get("verified") is not True:
        errors.append(f"{frame_id} lacks verified visual_verification")
        return errors
    method = str(verification.get("method") or "")
    if method not in VERIFIED_METHODS:
        errors.append(f"{frame_id} uses unsupported visual verification method: {method!r}")
    if method == "human" and not str(verification.get("reviewer") or "").strip():
        errors.append(f"{frame_id} human verification requires reviewer")
    provider = str(row.get("cloud_provider") or "").strip().casefold()
    model = str(row.get("cloud_model") or verification.get("model") or "").strip()
    if provider in MANUAL_PROVIDERS or model.casefold() in MANUAL_PROVIDERS:
        errors.append(f"{frame_id} contains unverified manual-analysis provenance")
    expected_sha = str(frame.get("sha256") or "").strip()
    input_sha = str(
        verification.get("input_sha256")
        or verification.get("frame_sha256")
        or ""
    ).strip()
    if expected_sha and input_sha != expected_sha:
        errors.append(f"{frame_id} input_sha256 does not match frame_manifest sha256")
    if method in {"cloud_vision", "local_vlm"} and not (model or verification.get("model")):
        errors.append(f"{frame_id} cloud/local visual verification requires model")
    return errors


def validate_visual_rows(
    job_dir: Path,
    *,
    frame_by_id: dict[str, dict[str, Any]] | None = None,
    visual_rows: Iterable[dict[str, Any]] | None = None,
    tolerance: float = TIMESTAMP_TOLERANCE_SECONDS,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Validate one-and-only-one visual row for every selected frame.

    ``failed`` rows are retained as uncovered evidence so callers can produce
    a useful coverage report.  Timestamp or provenance errors are hard errors.
    """
    frames, errors = load_frame_manifest(job_dir) if frame_by_id is None else (frame_by_id, [])
    visual_path = job_dir / "visual" / "visual_results.jsonl"
    if visual_rows is None:
        if not visual_path.exists():
            return frames, [], errors + [f"missing visual results: {visual_path}"]
        try:
            rows = list(read_jsonl(visual_path))
        except (OSError, ValueError) as exc:
            return frames, [], errors + [f"cannot read visual results: {exc}"]
    else:
        rows = list(visual_rows)

    by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"visual result row {index} is not an object")
            continue
        frame_id = str(row.get("frame_id") or "")
        if not frame_id:
            errors.append(f"visual result row {index} has no frame_id")
            continue
        if frame_id in by_id:
            errors.append(f"duplicate visual result for {frame_id}")
            continue
        by_id[frame_id] = row
        frame = frames.get(frame_id)
        if frame is None:
            errors.append(f"visual result references unknown frame {frame_id}")
            continue
        try:
            timestamp = _number(row.get("timestamp"), f"visual {frame_id}.timestamp")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if abs(timestamp - float(frame["timestamp"])) > tolerance:
            errors.append(
                f"{frame_id} timestamp {timestamp:.3f} does not match frame manifest "
                f"{float(frame['timestamp']):.3f}"
            )
        status = str(row.get("analysis_status") or "")
        if status not in ANALYSIS_STATUSES:
            errors.append(f"{frame_id} has unsupported analysis_status: {status!r}")
            continue
        if status == "failed":
            if not str(row.get("error") or row.get("failure_reason") or "").strip():
                errors.append(f"{frame_id} failed visual analysis without an error")
            continue
        errors.extend(_verification_errors(row, frame, frame_id=frame_id))
        if status == "analyzed" and not str(row.get("summary") or "").strip():
            errors.append(f"{frame_id} analyzed row requires a non-empty summary")
        if status == "not_needed":
            reason = str(row.get("skip_reason_code") or "")
            if reason not in SKIP_REASON_CODES:
                errors.append(f"{frame_id} has invalid skip_reason_code: {reason!r}")
            if reason == "duplicate":
                duplicate_of = str(row.get("duplicate_of") or "")
                if not duplicate_of or duplicate_of == frame_id or duplicate_of not in frames:
                    errors.append(f"{frame_id} duplicate row needs a different known duplicate_of")
            elif not str(row.get("skip_evidence") or row.get("review_note") or "").strip():
                errors.append(f"{frame_id} not_needed row requires skip_evidence or review_note")

    missing = sorted(set(frames) - set(by_id))
    if missing:
        errors.append(f"missing visual results for selected frames: {', '.join(missing)}")
    return frames, rows, errors


def _read_illustration_manifest(job_dir: Path) -> tuple[dict[str, Any], list[str]]:
    path = job_dir / "illustrations" / "manifest.json"
    if not path.exists():
        return {}, [f"missing illustration manifest: {path}"]
    try:
        payload = read_json(path)
    except (OSError, ValueError) as exc:
        return {}, [f"cannot read illustration manifest: {exc}"]
    if not isinstance(payload.get("items"), list):
        return payload, ["illustration manifest items must be a list"]
    return payload, []


def validate_illustrations(
    job_dir: Path,
    frame_by_id: dict[str, dict[str, Any]],
    visual_rows: list[dict[str, Any]],
    *,
    require_manifest: bool,
    tolerance: float,
) -> tuple[dict[str, Any], list[str]]:
    manifest_path = job_dir / "illustrations" / "manifest.json"
    if not manifest_path.exists() and not require_manifest:
        return {}, []
    manifest, errors = _read_illustration_manifest(job_dir)
    if errors:
        return manifest, errors
    row_by_id = {str(row.get("frame_id")): row for row in visual_rows}
    item_ids: set[str] = set()
    created_count = 0
    failed_count = 0
    for index, item in enumerate(manifest.get("items", []), start=1):
        if not isinstance(item, dict):
            errors.append(f"illustration item {index} is not an object")
            continue
        illustration_id = str(item.get("illustration_id") or "")
        frame_id = str(item.get("frame_id") or "")
        if not illustration_id or illustration_id in item_ids:
            errors.append(f"duplicate or missing illustration_id at item {index}")
        item_ids.add(illustration_id)
        frame = frame_by_id.get(frame_id)
        if frame is None:
            errors.append(f"{illustration_id} references unknown frame {frame_id}")
            continue
        try:
            timestamp = _number(item.get("timestamp"), f"{illustration_id}.timestamp")
            if abs(timestamp - float(frame["timestamp"])) > tolerance:
                errors.append(f"{illustration_id} timestamp does not match {frame_id}")
        except ValueError as exc:
            errors.append(str(exc))
        source_path = str(item.get("source_path") or "")
        if source_path != str(frame.get("selected_path") or ""):
            errors.append(f"{illustration_id} source_path does not match {frame_id}.selected_path")
        status = str(item.get("status") or "")
        if status == "created":
            created_count += 1
            path_text = str(item.get("path") or "")
            try:
                output = _relative_path(job_dir, path_text, f"{illustration_id}.path")
                if not (job_dir / output).is_file():
                    errors.append(f"{illustration_id} crop is missing: {output}")
                if not output.parts or output.parts[0] != "illustrations":
                    errors.append(f"{illustration_id} crop must live under illustrations/")
            except ValueError as exc:
                errors.append(str(exc))
            row = row_by_id.get(frame_id, {})
            region_ids = {
                str(region.get("region_id") or "")
                for region in row.get("illustrations", [])
                if isinstance(region, dict)
            }
            if str(item.get("region_id") or "") not in region_ids:
                errors.append(f"{illustration_id} region_id is not present in visual result {frame_id}")
        elif status == "failed":
            failed_count += 1
            if not str(item.get("error") or "").strip():
                errors.append(f"failed illustration {illustration_id} has no error")
        else:
            errors.append(f"{illustration_id} has unsupported status {status!r}")
    if int(manifest.get("created_count") or 0) != created_count:
        errors.append("illustration manifest created_count does not match items")
    if int(manifest.get("failed_count") or 0) != failed_count:
        errors.append("illustration manifest failed_count does not match items")
    return manifest, errors


def _segment_map(job_dir: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    path = job_dir / "evidence" / "segments.jsonl"
    if not path.exists():
        return {}, [f"missing evidence segments: {path}"]
    try:
        rows = list(read_jsonl(path))
    except (OSError, ValueError) as exc:
        return {}, [f"cannot read evidence segments: {exc}"]
    segments: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for row in rows:
        segment_id = str(row.get("segment_id") or "")
        if not segment_id or segment_id in segments:
            errors.append(f"duplicate or missing segment_id: {segment_id!r}")
        else:
            segments[segment_id] = row
    return segments, errors


def validate_bindings(
    job_dir: Path,
    frame_by_id: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    *,
    require_bindings: bool,
    tolerance: float,
) -> list[str]:
    created = [
        item for item in manifest.get("items", []) if item.get("status") == "created"
    ]
    path = job_dir / "evidence" / "illustration_bindings.jsonl"
    if not created and not path.exists():
        return []
    if not path.exists():
        return [f"missing illustration bindings: {path}"] if require_bindings else []
    try:
        rows = list(read_jsonl(path))
    except (OSError, ValueError) as exc:
        return [f"cannot read illustration bindings: {exc}"]
    segments, errors = _segment_map(job_dir)
    by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, start=1):
        illustration_id = str(row.get("illustration_id") or "")
        if not illustration_id or illustration_id in by_id:
            errors.append(f"duplicate or missing illustration binding at row {index}")
        else:
            by_id[illustration_id] = row
    for item in created:
        illustration_id = str(item.get("illustration_id"))
        binding = by_id.get(illustration_id)
        if binding is None:
            errors.append(f"missing binding for {illustration_id}")
            continue
        if binding.get("verified") is not True:
            errors.append(f"binding {illustration_id} is not verified")
        frame_id = str(item.get("frame_id") or "")
        if str(binding.get("frame_id") or "") != frame_id:
            errors.append(f"binding {illustration_id} frame_id mismatch")
        try:
            binding_time = _number(binding.get("timestamp"), f"binding {illustration_id}.timestamp")
            if abs(binding_time - float(frame_by_id[frame_id]["timestamp"])) > tolerance:
                errors.append(f"binding {illustration_id} timestamp mismatch")
        except (KeyError, ValueError) as exc:
            errors.append(str(exc))
        if str(binding.get("caption") or "").strip() != str(item.get("caption") or "").strip():
            errors.append(f"binding {illustration_id} caption mismatch")
        segment_id = str(binding.get("segment_id") or "")
        segment = segments.get(segment_id)
        if segment is None:
            errors.append(f"binding {illustration_id} references unknown segment {segment_id!r}")
            continue
        segment_frames = {
            str(frame.get("frame_id")): frame
            for frame in segment.get("frames", [])
            if isinstance(frame, dict)
        }
        if frame_id not in segment_frames:
            errors.append(f"binding {illustration_id} frame is not attached to {segment_id}")
        elif abs(float(segment_frames[frame_id].get("timestamp", -1)) - float(item.get("timestamp", -2))) > tolerance:
            errors.append(f"binding {illustration_id} segment frame timestamp mismatch")
        if not str(binding.get("note_anchor") or "").strip():
            errors.append(f"binding {illustration_id} requires note_anchor")
    if require_bindings:
        orphaned = sorted(set(by_id) - {str(item.get("illustration_id")) for item in created})
        if orphaned:
            errors.append(f"orphan illustration bindings: {', '.join(orphaned)}")
    return errors


def _read_placement_map(job_dir: Path) -> dict[str, dict[str, Any]]:
    path = job_dir / "evidence" / "illustration_placement.json"
    if not path.exists():
        return {}
    try:
        payload = read_json(path)
    except (OSError, ValueError):
        return {}
    placement: dict[str, dict[str, Any]] = {}
    for item in payload.get("items", []):
        if isinstance(item, dict):
            illustration_id = str(item.get("illustration_id") or "")
            if illustration_id:
                placement[illustration_id] = item
    return placement


def validate_placement_consistency(
    job_dir: Path,
    manifest: dict[str, Any],
) -> list[str]:
    """Bindings must agree with the deterministic placement map.

    ``illustration_placement.json`` records the single segment each frame was
    bound to.  If a binding claims a different segment, the writer attached the
    crop to a claim it does not belong to.  When the placement map is absent
    (older jobs or synthetic fixtures) this check is skipped.
    """
    placement = _read_placement_map(job_dir)
    if not placement:
        return []
    bindings_path = job_dir / "evidence" / "illustration_bindings.jsonl"
    if not bindings_path.exists():
        return []
    try:
        rows = list(read_jsonl(bindings_path))
    except (OSError, ValueError) as exc:
        return [f"cannot read illustration bindings: {exc}"]
    binding_by_id = {str(row.get("illustration_id") or ""): row for row in rows}
    errors: list[str] = []
    for item in manifest.get("items", []):
        if item.get("status") != "created":
            continue
        illustration_id = str(item.get("illustration_id") or "")
        expected = placement.get(illustration_id)
        binding = binding_by_id.get(illustration_id)
        if expected is None or binding is None:
            continue
        expected_segment = str(expected.get("primary_segment_id") or "")
        bound_segment = str(binding.get("segment_id") or "")
        if expected_segment and bound_segment and expected_segment != bound_segment:
            errors.append(
                f"binding {illustration_id} segment {bound_segment} does not match "
                f"placement segment {expected_segment}"
            )
    return errors


def _strip_image_alt(text: str) -> str:
    """Blank out image embeds (keeping length) so anchor matches land in prose."""

    def blank(match: re.Match[str]) -> str:
        return " " * len(match.group(0))

    return MARKDOWN_IMAGE_RE.sub(blank, text)


def validate_note_placement(
    job_dir: Path,
    manifest: dict[str, Any],
    *,
    require_note: bool,
    max_anchor_distance: int = 800,
) -> list[str]:
    """Every created crop is embedded exactly once, near its bound anchor text."""
    if not require_note:
        return []
    note_path = job_dir / "note" / "note.md"
    if not note_path.exists():
        return [f"missing note: {note_path}"]
    created = [item for item in manifest.get("items", []) if item.get("status") == "created"]
    if not created:
        return []
    text = note_path.read_text(encoding="utf-8")
    prose = _strip_image_alt(text)

    bindings_path = job_dir / "evidence" / "illustration_bindings.jsonl"
    binding_by_id: dict[str, dict[str, Any]] = {}
    if bindings_path.exists():
        try:
            binding_by_id = {
                str(row.get("illustration_id") or ""): row
                for row in read_jsonl(bindings_path)
            }
        except (OSError, ValueError) as exc:
            return [f"cannot read illustration bindings: {exc}"]

    embed_positions: list[tuple[str, int]] = []
    for match in MARKDOWN_IMAGE_RE.finditer(text):
        target = match.group(1).strip().strip("<>")
        if target.startswith(("http://", "https://", "data:")):
            continue
        resolved = str((note_path.parent / target).resolve())
        embed_positions.append((resolved, match.start()))

    errors: list[str] = []
    for item in created:
        illustration_id = str(item.get("illustration_id") or "")
        crop_path = str((job_dir / str(item.get("path") or "")).resolve())
        hits = [position for path, position in embed_positions if path == crop_path]
        if not hits:
            errors.append(f"note does not embed created illustration {illustration_id}")
            continue
        if len(hits) > 1:
            errors.append(f"illustration {illustration_id} is embedded {len(hits)} times")
        anchor = str(binding_by_id.get(illustration_id, {}).get("note_anchor") or "").strip()
        if not anchor:
            # The binding audit already reports the missing anchor; avoid a
            # duplicate error here.
            continue
        anchor_positions = [
            index
            for index in _find_all(prose, anchor)
        ]
        if not anchor_positions:
            errors.append(
                f"note_anchor for {illustration_id} is not present in the note body: {anchor!r}"
            )
            continue
        embed_at = hits[0]
        nearest = min(abs(embed_at - index) for index in anchor_positions)
        if nearest > max_anchor_distance:
            errors.append(
                f"illustration {illustration_id} is placed far from its anchor "
                f"(nearest {nearest} chars > {max_anchor_distance})"
            )
    return errors


def _find_all(text: str, needle: str) -> list[int]:
    if not needle:
        return []
    positions: list[int] = []
    start = text.find(needle)
    while start != -1:
        positions.append(start)
        start = text.find(needle, start + 1)
    return positions


def validate_note_links(
    job_dir: Path,
    manifest: dict[str, Any],
    *,
    require_note: bool,
) -> list[str]:
    note_path = job_dir / "note" / "note.md"
    if not require_note:
        return []
    if not note_path.exists():
        return [f"missing note: {note_path}"]
    text = note_path.read_text(encoding="utf-8")
    targets = re.findall(r"!\[[^\]]*\]\((<[^>]+>|[^\)]+)\)", text)
    target_paths = {
        str((note_path.parent / target.strip().strip("<>")).resolve())
        for target in targets
        if not target.strip().strip("<>").startswith(("http://", "https://", "data:"))
    }
    created_by_path = {}
    for item in manifest.get("items", []):
        if item.get("status") == "created":
            path = str((job_dir / str(item.get("path") or "")).resolve())
            created_by_path[path] = str(item.get("illustration_id") or "")
    errors: list[str] = []
    for target in sorted(target_paths):
        if Path(target).is_relative_to(job_dir / "illustrations") and target not in created_by_path:
            errors.append(f"note references unregistered illustration: {target}")
    return errors


def build_report(
    job_dir: Path,
    *,
    require_illustrations: bool = False,
    require_bindings: bool = False,
    require_note: bool = False,
    tolerance: float = TIMESTAMP_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    job_dir = job_dir.resolve()
    checks: list[dict[str, Any]] = []
    frames, visual_rows, row_errors = validate_visual_rows(job_dir, tolerance=tolerance)
    _check(
        checks,
        "visual_rows_timestamp_and_provenance",
        not row_errors,
        "; ".join(row_errors) if row_errors else f"{len(visual_rows)}/{len(frames)} rows verified",
    )
    manifest, illustration_errors = validate_illustrations(
        job_dir,
        frames,
        visual_rows,
        require_manifest=require_illustrations,
        tolerance=tolerance,
    )
    _check(
        checks,
        "illustration_frame_binding",
        not illustration_errors,
        "; ".join(illustration_errors) if illustration_errors else "illustration timestamps and source paths match frame manifest",
    )
    binding_errors = validate_bindings(
        job_dir,
        frames,
        manifest,
        require_bindings=require_bindings,
        tolerance=tolerance,
    )
    _check(
        checks,
        "illustration_semantic_binding",
        not binding_errors,
        "; ".join(binding_errors) if binding_errors else "illustrations are bound to verified evidence segments",
    )
    placement_errors = validate_placement_consistency(job_dir, manifest)
    _check(
        checks,
        "illustration_placement_consistency",
        not placement_errors,
        "; ".join(placement_errors) if placement_errors else "bindings match the deterministic placement map",
    )
    note_errors = validate_note_links(job_dir, manifest, require_note=require_note)
    _check(
        checks,
        "note_illustration_links",
        not note_errors,
        "; ".join(note_errors) if note_errors else "note illustration links resolve to registered crops",
    )
    note_placement_errors = validate_note_placement(
        job_dir, manifest, require_note=require_note
    )
    _check(
        checks,
        "note_illustration_placement",
        not note_placement_errors,
        "; ".join(note_placement_errors)
        if note_placement_errors
        else "each crop is embedded once near its bound anchor",
    )
    passed = all(check["passed"] for check in checks)
    return {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "passed": passed,
        "timestamp_source": "frames/frame_manifest.json",
        "timestamp_tolerance_seconds": tolerance,
        "selected_frame_count": len(frames),
        "visual_result_count": len(visual_rows),
        "illustrations_created": int(manifest.get("created_count") or 0),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--require-illustrations", action="store_true")
    parser.add_argument("--require-bindings", action="store_true")
    parser.add_argument("--require-note", action="store_true")
    parser.add_argument("--tolerance", type=float, default=TIMESTAMP_TOLERANCE_SECONDS)
    args = parser.parse_args()
    if args.tolerance < 0:
        parser.error("--tolerance must be non-negative")
    job_dir = args.job.expanduser().resolve()
    report = build_report(
        job_dir,
        require_illustrations=args.require_illustrations or args.require_bindings,
        require_bindings=args.require_bindings,
        require_note=args.require_note,
        tolerance=args.tolerance,
    )
    path = job_dir / "metadata" / "audit_visual.json"
    write_json(path, report)
    print(f"visual-evidence: {'PASS' if report['passed'] else 'FAIL'}")
    for check in report["checks"]:
        print(f"- {'PASS' if check['passed'] else 'FAIL'} {check['name']}: {check['detail']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
