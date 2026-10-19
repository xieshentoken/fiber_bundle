"""Bounded direct-file video/audio selections; no manifest or downloader-managed merging."""

import math
from typing import Any
from urllib.parse import urlsplit

from .media_link_failure import AcquisitionBlocked


def parts(info: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    selected = info.get("requested_formats")
    if selected is None:
        if info.get("protocol") != "https":
            raise AcquisitionBlocked("format_unsupported")
        return [info]
    if (
        mode != "full_evidence"
        or info.get("protocol") != "https+https"
        or not isinstance(selected, list)
        or len(selected) != 2
        or any(not isinstance(p, dict) for p in selected)
    ):
        raise AcquisitionBlocked("format_unsupported")
    video, audio = selected
    for p in selected:
        try:
            address = urlsplit(p.get("url", ""))
            direct = (
                address.scheme == "https"
                and address.hostname
                and address.port in (None, 443)
                and not address.username
                and not address.password
                and not address.fragment
            )
        except (TypeError, ValueError):
            direct = False
        if (
            not direct
            or p.get("protocol") != "https"
            or any(
                p.get(key)
                for key in ("requested_formats", "fragments", "manifest_url", "has_drm", "is_live")
            )
        ):
            raise AcquisitionBlocked("format_unsupported")
    if (
        video.get("ext") != "mp4"
        or not isinstance(video.get("vcodec"), str)
        or video["vcodec"] in ("", "none")
        or video.get("acodec") != "none"
        or type(video.get("height")) is not int
        or not 1 <= video["height"] <= 720
        or type(video.get("width")) is not int
        or not 1 <= video["width"] <= 1920
        or audio.get("ext") != "m4a"
        or audio.get("vcodec") != "none"
        or not isinstance(audio.get("acodec"), str)
        or audio["acodec"] in ("", "none")
        or video["url"] == audio["url"]
    ):
        raise AcquisitionBlocked("format_unsupported")
    return [video, audio]


def reported_size(info: dict[str, Any], mode: str) -> tuple[int | None, str | None]:
    total = 0
    basis = "reported"
    for p in parts(info, mode):
        amount = None
        for key in ("filesize", "filesize_approx"):
            value = p.get(key)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and 0 < value <= 2**53
                and math.isfinite(value)
            ):
                amount = math.ceil(value)
                if key == "filesize_approx":
                    basis = "estimated"
                break
        if amount is None:
            return None, None
        total += amount
    return (total, basis) if total <= 2**53 else (None, None)


def leaf(info: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    # Copy only download fields; nested formats, sections, playlists and postprocessors
    # from the combined result never reach process_info. Input dictionaries stay intact.
    metadata: dict[str, Any] = {
        k: info[k]
        for k in ("id", "title", "duration", "webpage_url", "extractor", "extractor_key")
        if k in info
    }
    metadata.update(
        {
            k: p[k]
            for k in (
                "url",
                "format_id",
                "ext",
                "protocol",
                "filesize",
                "filesize_approx",
                "vcodec",
                "acodec",
                "http_headers",
                "width",
                "height",
                "tbr",
                "abr",
                "fps",
                "asr",
                "audio_channels",
            )
            if k in p
        }
    )
    return metadata
