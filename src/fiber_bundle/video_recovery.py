"""Explicit configuration refresh before any video extraction or paid model work."""

from typing import Any

from .config import Config, InvalidInput, contained


def refresh(config: Config, link: dict[str, Any]) -> None:
    frozen = link.get("video_processing")
    if (
        not frozen
        or frozen.get("completed")
        or frozen.get("source_resolution")
        or link.get("receipt")
        or link.get("publication")
        or any(
            frozen.get(k)
            for k in (
                "vision_runs",
                "note_runs",
                "asr_runs",
                "previous_vision_runs",
                "previous_note_runs",
                "previous_asr_runs",
            )
        )
        or contained(config.state_root, "video-skill-jobs/" + frozen["job_id"]).exists()
    ):
        raise InvalidInput("intake_video_refresh_not_safe")
    link.setdefault("previous_video_processing", []).append(frozen)
    del link["video_processing"]
