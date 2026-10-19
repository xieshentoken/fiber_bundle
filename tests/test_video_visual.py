"""Visual fallback requires real, full audio processing without a usable transcript."""

import json
import wave

import pytest

from fiber_bundle import video_visual


@pytest.fixture
def empty_asr(tmp_path):
    skill, job = tmp_path / "skill", tmp_path / "job"
    (skill / "scripts").mkdir(parents=True)
    (skill / "scripts/qwen3_asr_backend.py").write_text("frozen backend")
    (job / "source").mkdir(parents=True)
    (job / "transcript").mkdir()
    (job / "source/video.mp4").write_bytes(b"unchanged source media")
    (job / "transcript/asr_prompt.txt").write_text("API, ASR")
    audio = job / "transcript/asr_audio.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x00\x00" * 32000)
    video_visual.write(
        job / "metadata/source_coverage.json",
        {
            "complete": True,
            "media_path": "source/video.mp4",
            "acquired_duration_seconds": 2,
        },
    )
    video_visual.write(
        job / "transcript/asr_diagnostic.json",
        {
            "schema": "fiber-v3-asr-diagnostic/v1",
            "source_media_sha256": video_visual.checksum(job / "source/video.mp4"),
            "audio_sha256": video_visual.checksum(audio),
            "context_sha256": video_visual.checksum(job / "transcript/asr_prompt.txt"),
            "backend_sha256": video_visual.checksum(skill / "scripts/qwen3_asr_backend.py"),
            "transcript_segments": 0,
            "transcript_characters": 0,
            "chunks": [{"chunk_index": 1, "start": 0, "end": 2, "status": "empty"}],
        },
    )
    return skill, job


def test_empty_audio_does_not_become_fake_speech_or_claim_speech_verified(empty_asr):
    skill, job = empty_asr
    result = video_visual.qualify(skill, job)
    assert result["reason"] == "all_asr_chunks_empty"
    assert result["speech_verified"] is False
    assert "未核验语音" in result["disclosure"]
    assert not list((job / "transcript").glob("*.srt"))


@pytest.mark.parametrize(
    "change",
    [
        {"chunks": []},
        {"chunks": [{"chunk_index": 1, "start": 0, "end": 2, "status": "failed"}]},
        {"chunks": [{"chunk_index": 1, "start": 0, "end": 2, "status": "complete"}]},
        {"chunks": [{"chunk_index": 1, "start": 0, "end": 1, "status": "empty"}]},
        {"chunks": [{"chunk_index": 1, "start": 1, "end": 2, "status": "empty"}]},
        {"chunks": [{"chunk_index": 1, "start": 0, "end": float("nan"), "status": "empty"}]},
        {"transcript_segments": 1},
        {"transcript_characters": 20},
    ],
)
def test_errors_partial_passes_and_gaps_never_trigger_fallback(empty_asr, change):
    skill, job = empty_asr
    path = job / "transcript/asr_diagnostic.json"
    report = json.loads(path.read_text())
    report.update(change)
    video_visual.write(path, report)
    with pytest.raises(ValueError, match="visual_fallback_invalid"):
        video_visual.qualify(skill, job)


@pytest.mark.parametrize(
    "relative", ["source/video.mp4", "transcript/asr_prompt.txt", "transcript/asr_audio.wav"]
)
def test_modified_source_audio_or_terms_cannot_reuse_diagnostic(empty_asr, relative):
    skill, job = empty_asr
    with (job / relative).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="visual_fallback_invalid"):
        video_visual.qualify(skill, job)


def test_usable_transcript_keeps_original_route(empty_asr):
    skill, job = empty_asr
    (job / "transcript/subtitle_normalized.srt").write_text("existing subtitle")
    with pytest.raises(ValueError, match="visual_fallback_invalid"):
        video_visual.qualify(skill, job)


@pytest.mark.parametrize("duration", [4, 71.019, 3600, 43200])
def test_visual_sampling_uses_bounded_exact_source_timestamps(empty_asr, duration):
    _, job = empty_asr
    video_visual.write(
        job / "metadata/source_coverage.json", {"acquired_duration_seconds": duration}
    )
    args = video_visual.frame_arguments(job, job / "source/video.mp4")
    stamps = [float(x) for x in args[args.index("--timestamps") + 1].split(",")]
    assert stamps[0] == 0 and len(stamps) <= 120
    assert all(0 <= t < duration for t in stamps)
    assert len(stamps) == len(set(stamps))
