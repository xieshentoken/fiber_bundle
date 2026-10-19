#!/usr/bin/env python3
"""Create or update the shared Qwen3-ASR environment used by note skills."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def venv_python(venv: Path) -> Path:
    windows = venv / "Scripts" / "python.exe"
    return windows if sys.platform == "win32" else venv / "bin" / "python"


def run(command: list[str], dry_run: bool, timeout: float | None = None) -> None:
    print(" ".join(command))
    if not dry_run:
        subprocess.run(command, check=True, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    cache_value = os.environ.get("BILI_NOTE_QWEN_CACHE") or os.environ.get(
        "RIMAGINATION_NOTE_CACHE"
    )
    shared_cache = (
        Path(cache_value).expanduser()
        if cache_value
        else Path.home() / ".cache" / "bili-note-v2"
    )
    parser.add_argument("--venv", type=Path, default=shared_cache / "qwen3-asr-venv")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    args.venv.parent.mkdir(parents=True, exist_ok=True)
    python = venv_python(args.venv)
    if not python.exists():
        run([args.python, "-m", "venv", "--system-site-packages", str(args.venv)], args.dry_run)
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"], args.dry_run)
    run([str(python), "-m", "pip", "install", "--upgrade", "qwen-asr", "accelerate"], args.dry_run)
    if not args.dry_run:
        run(
            [str(python), "-c", "import qwen_asr, soundfile, torch; print(qwen_asr.__file__)"],
            False,
            timeout=60,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
