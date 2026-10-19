"""Bounded version and pinned-source checks; no acquisition, ASR or browser-state access."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

COMMANDS = {
    "ffmpeg": ["-nostdin", "-version"],
    "ffprobe": ["-version"],
    "yt-dlp": ["--ignore-config", "--no-plugin-dirs", "--no-update", "--version"],
    "deno": ["--version"],
    "node": ["--version"],
    "docker": ["--version"],
}
PATTERNS = {
    "ffmpeg": r"ffmpeg version ([A-Za-z0-9_.+-]{1,80})(?:\s|$)",
    "ffprobe": r"ffprobe version ([A-Za-z0-9_.+-]{1,80})(?:\s|$)",
    "yt-dlp": r"(\d{4}\.\d{2}\.\d{2}(?:\.[A-Za-z0-9.-]{1,40})?)(?:\s|$)",
    "deno": r"deno (\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]{1,40})?)(?:\s|$)",
    "node": r"v(\d+\.\d+\.\d+)(?:\s|$)",
    "docker": r"Docker version (\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]{1,40})?),",
}


def _digest(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
        raise OSError("media_tool_or_reference_size")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def source_status(root: Path | None) -> dict[str, Any]:
    manifest = json.loads((Path(__file__).parent / "vendor/bili-note-source.json").read_text())
    result: dict[str, Any] = {
        "state": "NOT_RUN",
        "matched_files": 0,
        "expected_files": len(manifest["source_files_sha256"]),
        "code_copied": False,
        "upstream_license": manifest["license"],
        "skill_frontmatter_name": manifest["skill_frontmatter_name"],
    }
    if root is None:
        return result
    root = root.absolute()
    try:
        if root.resolve() != root or not root.is_dir():
            result["state"] = "BLOCKED"
            return result
        for name, expected in manifest["source_files_sha256"].items():
            path = root / name
            if (
                path.resolve() != path
                or not path.is_file()
                or path.stat().st_size != manifest["source_files_bytes"][name]
                or _digest(path) != expected
            ):
                result["state"] = "BLOCKED"
                return result
            result["matched_files"] += 1
    except OSError:
        result["state"] = "BLOCKED"
        return result
    result["state"] = "PASS"
    return result


def inspect(root: Path | None = None) -> dict[str, Any]:
    tools = {}
    with tempfile.TemporaryDirectory(prefix="fiber-media-versions-") as directory:
        # Exclude inherited secrets and the real HOME/config. Version probes are not
        # a network sandbox or permission to run these tools over untrusted media.
        env = {
            "PATH": os.defpath,
            "HOME": directory,
            "LANG": "C",
            "DENO_NO_UPDATE_CHECK": "1",
            "DENO_DIR": directory,
        }
        for name, arguments in COMMANDS.items():
            executable = shutil.which(name)
            if executable is None:
                tools[name] = {"state": "MISSING", "version": None, "reason": "tool_not_found"}
                continue
            try:
                completed = subprocess.run(
                    [executable, *arguments],
                    cwd=directory,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                text = completed.stdout.decode("ascii", errors="strict")
                match = re.match(PATTERNS[name], text) if len(text) <= 16384 else None
                if completed.returncode != 0 or match is None:
                    tools[name] = {
                        "state": "BLOCKED",
                        "version": None,
                        "reason": "tool_version_failed"
                        if completed.returncode
                        else "version_output_unrecognized",
                    }
                else:
                    tools[name] = {
                        "state": "PASS",
                        "version": match[1],
                        "executable_sha256": _digest(Path(executable)),
                    }
            except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
                tools[name] = {
                    "state": "BLOCKED",
                    "version": None,
                    "reason": "tool_version_timeout"
                    if isinstance(exc, subprocess.TimeoutExpired)
                    else "version_output_encoding"
                    if isinstance(exc, UnicodeError)
                    else "tool_file_or_spawn_unavailable",
                }
    return {
        "schema": "fiber-media-capabilities/v1",
        "tools": tools,
        "source_reference": source_status(root),
        "routes": {
            name: {"state": "NOT_RUN", "reason": reason}
            for name, reason in {
                "direct_audio_subtitle": "receipt_binding_and_isolated_media_probe_not_integrated",
                "direct_video_subtitle": "isolated_probe_and_visual_review_not_integrated",
                "bilibili_public_subtitle": "real_platform_acquisition_not_tested",
                "youtube_public_subtitle": "real_platform_and_js_challenge_not_tested",
                "local_asr": "backend_weights_and_confirmed_glossary_not_qualified",
                "authenticated_browser": "dedicated_connector_and_safe_handoff_not_tested",
                "visual_evidence": "frame_extraction_and_analysis_not_qualified",
            }.items()
        },
        "scope": "version_and_source_checks_only",
        "container_media_probe": "NOT_RUN",
        "ready_for_pipeline": False,
        "model_calls": 0,
    }
