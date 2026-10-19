"""Run frozen V3 ASR unchanged while retaining finite, hash-bound chunk diagnostics."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def checksum(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while raw := stream.read(1024 * 1024):
            value.update(raw)
    return value.hexdigest()


def run(skill: Path, args: list[str]) -> int:
    sys.path.insert(0, str(skill / "scripts"))
    sys.argv = [str(skill / "scripts/run_asr.py"), *args]
    import run_asr  # type: ignore[import-not-found]

    job = Path(args[0])
    media = run_asr.resolve_media(
        job, Path(args[args.index("--media") + 1]) if "--media" in args else None
    )

    def backend(
        python: Path,
        skill_dir: Path,
        audio: Path,
        output: Path,
        context_file: Path,
        model: str,
        language: str,
        device_map: str,
        dtype: str,
        chunk_seconds: float,
        chunk_overlap_seconds: float,
    ) -> None:
        diagnostic = output.parent / "asr_diagnostic.json"
        if diagnostic.exists():
            history = output.parent / "asr_diagnostics"
            history.mkdir(exist_ok=True)
            diagnostic.replace(history / (checksum(diagnostic) + ".json"))
        command = [
            str(python),
            str(Path(__file__)),
            "backend",
            str(skill_dir),
            str(media),
            "--audio",
            str(audio),
            "--out",
            str(output),
            "--context-file",
            str(context_file),
            "--model",
            model,
            "--language",
            language,
            "--device-map",
            device_map,
            "--dtype",
            dtype,
            "--chunk-seconds",
            str(chunk_seconds),
            "--chunk-overlap-seconds",
            str(chunk_overlap_seconds),
        ]
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("Qwen3-ASR failed: " + result.stderr[-2400:])

    run_asr.run_qwen = backend
    sys.argv = [str(skill / "scripts/run_asr.py"), *args]
    return int(run_asr.main())


def capture(skill: Path, media: Path, args: list[str]) -> int:
    sys.path.insert(0, str(skill / "scripts"))
    import qwen3_asr_backend as backend  # type: ignore[import-not-found]

    original = backend.transcribe_chunks
    audio = Path(args[args.index("--audio") + 1])
    context = Path(args[args.index("--context-file") + 1])
    output = Path(args[args.index("--out") + 1]).parent / "asr_diagnostic.json"

    def transcribe(*values: Any, **options: Any) -> Any:
        segments, chunks = original(*values, **options)
        report = {
            "schema": "fiber-v3-asr-diagnostic/v1",
            "source_media_sha256": checksum(media),
            "audio_sha256": checksum(audio),
            "context_sha256": checksum(context),
            "backend_sha256": checksum(skill / "scripts/qwen3_asr_backend.py"),
            "transcript_segments": len(segments),
            "transcript_characters": sum(len(s.get("text", "")) for s in segments),
            "chunks": [
                {key: row[key] for key in ("chunk_index", "start", "end", "status")}
                for row in chunks
            ],
        }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        output.chmod(0o600)
        return segments, chunks

    backend.transcribe_chunks = transcribe
    sys.argv = [str(skill / "scripts/qwen3_asr_backend.py"), *args]
    return int(backend.main())


if __name__ == "__main__":
    mode, root, *arguments = sys.argv[1:]
    if mode == "run":
        raise SystemExit(run(Path(root), arguments))
    if mode == "backend":
        raise SystemExit(capture(Path(root), Path(arguments[0]), arguments[1:]))
    raise SystemExit(2)
