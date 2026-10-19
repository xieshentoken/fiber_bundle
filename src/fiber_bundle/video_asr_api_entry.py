"""Fixed V3 audio preparation/normalization; no provider access or model loading."""

import json
import sys
from pathlib import Path


def main(skill: Path, job: Path, mode: str) -> None:
    sys.path.insert(0, str(skill / "scripts"))
    import run_asr  # type: ignore[import-not-found]
    from common import (  # type: ignore[import-not-found]
        register_managed_file,
        render_srt,
        update_pipeline_state,
        write_json,
    )
    from normalize_transcript import normalize_transcript_file  # type: ignore[import-not-found]

    audio = job / "transcript/asr_audio.wav"
    if mode == "prepare":
        context = json.loads((job / "terms/asr_context.json").read_bytes())
        prompt = run_asr.build_asr_context(context)
        (job / "transcript/asr_prompt.txt").write_text(prompt + "\n")
        run_asr.extract_audio(run_asr.resolve_media(job, None), audio, False)
        register_managed_file(job, audio, kind="derived_audio", origin="ffmpeg-asr")
    elif mode == "finish":
        payload = json.loads((job / "transcript/asr_backend_raw.json").read_bytes())
        cues = run_asr.segments_to_cues(payload)
        coverage = run_asr.build_coverage_report(
            cues, run_asr.probe_audio_duration(audio), payload["chunk_results"]
        )
        write_json(job / "transcript/asr_coverage.json", coverage)
        generated = job / "transcript/asr_generated.srt"
        generated.write_text(render_srt(cues))
        normalize_transcript_file(job, generated, "asr")
        write_json(
            job / "transcript/asr_run.json",
            {
                "backend": "transcription-api",
                "timestamp_precision": "30-second-chunk",
                "model": payload["model"],
                "coverage": "transcript/asr_coverage.json",
            },
        )
        update_pipeline_state(job, "asr", "complete", "API transcript; chunk-level timestamps")
    else:
        raise ValueError("video_asr_mode_invalid")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
