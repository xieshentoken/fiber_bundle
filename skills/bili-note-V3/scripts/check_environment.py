#!/usr/bin/env python3
"""Report local capabilities without installing or changing dependencies."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys

from run_asr import find_qwen_python


def configured_browser_backends(values: list[str] | None = None) -> list[str]:
    raw_values = values if values else os.environ.get("BILI_NOTE_BROWSER_BACKENDS", "").split(",")
    aliases = {
        "edge": "edge",
        "msedge": "edge",
        "microsoft-edge": "edge",
        "chrome": "chrome",
        "google-chrome": "chrome",
        "generic": "generic_browser",
        "generic_browser": "generic_browser",
    }
    return sorted({aliases[item.strip().lower()] for item in raw_values if item.strip().lower() in aliases})


def build_report(
    tools: dict[str, str | None],
    qwen_python: str | None,
    browser_backends: list[str] | None = None,
) -> dict:
    media_ready = bool(tools["ffmpeg"] and tools["ffprobe"] and tools["yt-dlp"])
    youtube_js_ready = bool(tools.get("deno") or tools.get("node"))
    asr_ready = bool(qwen_python)
    capabilities = {
        "job_preflight": True,
        "terminology_review": True,
        "subtitle_normalization": True,
        "public_media_download": bool(tools["yt-dlp"]),
        "youtube_js_runtime": tools.get("deno") or tools.get("node"),
        "keyframe_extraction": bool(tools["ffmpeg"] and tools["ffprobe"]),
        "qwen3_asr": asr_ready,
        "authenticated_browser_handoff": "connector_required",
        "cloud_frame_analysis": "agent_runtime_required",
        "browser_backends_configured": browser_backends or [],
    }
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "tools": tools,
        "asr": {
            "qwen3_available": asr_ready,
            "qwen3_python": qwen_python,
            "setup_command": "python3 scripts/setup_asr.py",
        },
        "capabilities": capabilities,
        "browser_backends": browser_backends or [],
        "ready_for_subtitle_pipeline": media_ready,
        "ready_for_no_subtitle_pipeline": media_ready and asr_ready,
        "ready_for_full_pipeline": media_ready and asr_ready,
        "ready_for_youtube_public_pipeline": media_ready and youtube_js_ready,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--browser-backend",
        action="append",
        default=[],
        help="Declare a connector backend for this invocation: edge, chrome, or generic_browser",
    )
    args = parser.parse_args()

    tools = {
        name: shutil.which(name)
        for name in ("python3", "ffmpeg", "ffprobe", "yt-dlp", "deno", "node")
    }
    qwen = find_qwen_python()
    browser_backends = configured_browser_backends(args.browser_backend)
    report = build_report(tools, str(qwen) if qwen else None, browser_backends)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Bili Note V3 environment (Bilibili + YouTube)")
        for name, path in tools.items():
            print(f"- {name}: {path or 'MISSING'}")
        print(
            "- public video with platform subtitles: "
            + ("READY" if report["ready_for_subtitle_pipeline"] else "PARTIAL")
        )
        print(
            "- public video without subtitles: "
            + ("READY" if report["ready_for_no_subtitle_pipeline"] else "PARTIAL")
        )
        print(
            "- full local media + ASR pipeline: "
            + ("READY" if report["ready_for_full_pipeline"] else "PARTIAL")
        )
        print(
            "- public YouTube extraction: "
            + ("READY" if report["ready_for_youtube_public_pipeline"] else "PARTIAL")
        )
        if not tools["ffmpeg"] or not tools["ffprobe"]:
            print("  Keyframe extraction is unavailable until ffmpeg and ffprobe are installed.")
        if not tools["yt-dlp"]:
            print("  Public media acquisition is unavailable until yt-dlp is installed.")
        if not tools["deno"] and not tools["node"]:
            print("  YouTube extraction requires a supported Deno or Node JavaScript runtime.")
        if not report["asr"]["qwen3_available"]:
            print("  No-subtitle videos require Qwen3-ASR; run python3 scripts/setup_asr.py.")
        else:
            print(f"  Qwen3-ASR: {report['asr']['qwen3_python']}")
        configured = ", ".join(browser_backends) if browser_backends else "none"
        print(f"  Browser handoff backends configured: {configured} (no browser state is read by V3).")
        print("  Browser authentication and cloud vision require an active connector/runtime.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
