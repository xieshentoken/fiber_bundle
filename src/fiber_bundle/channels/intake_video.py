"""Page metadata and already published captions; never media downloads, OCR or ASR."""

import json
import math
import re
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .. import article_fetch, media_subtitles
from ..config import InvalidInput
from ..inputs import AssetInput, canonical
from ..objects import digest
from .intake_fetch import Content

MAX_PAGE = 8 * 1024 * 1024


def embedded(page: str, name: str) -> dict[str, Any]:
    match = re.search(re.escape(name) + r"\s*=\s*", page)
    if match is None:
        raise InvalidInput("intake_video_metadata_unavailable")
    try:
        value, _ = json.JSONDecoder().raw_decode(page[match.end() :])
    except (ValueError, RecursionError):
        raise InvalidInput("intake_video_metadata_invalid") from None
    if not isinstance(value, dict):
        raise InvalidInput("intake_video_metadata_invalid")
    return value


class Titles(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        value = dict(attrs)
        if tag == "meta" and value.get("property") == "og:title":
            self.title = (value.get("content") or "")[:1000]


def duration(value: Any, *, long_video: bool = False) -> int:
    if isinstance(value, bool):
        raise InvalidInput("intake_video_duration_unverified")
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        raise InvalidInput("intake_video_duration_unverified") from None
    if not math.isfinite(seconds) or seconds <= 0 or (not long_video and seconds > 60):
        raise InvalidInput("intake_video_duration_over_limit")
    if seconds > (2**53 - 1) / 1000:
        raise InvalidInput("intake_video_duration_unverified")
    return round(seconds * 1000)


def youtube(
    url: str, page: str, fetch: Callable[..., article_fetch.Response], *, long_video: bool = False
) -> tuple[dict[str, Any], bytes | None]:
    parsed = urlsplit(url)
    identity = parse_qs(parsed.query).get("v", [""])[0]
    if parsed.path.startswith("/shorts/"):
        identity = parsed.path.split("/")[2]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identity):
        raise InvalidInput("intake_video_single_resource_required")
    info = embedded(page, "ytInitialPlayerResponse")
    details = info.get("videoDetails", {})
    if (
        info.get("playabilityStatus", {}).get("status") != "OK"
        or details.get("videoId") != identity
    ):
        raise InvalidInput("intake_video_access_restricted")
    if details.get("isLiveContent"):
        raise InvalidInput("intake_video_live_unsupported")
    meta: dict[str, Any] = {
        "title": str(details.get("title", ""))[:1000],
        "description": str(details.get("shortDescription", ""))[:16000],
    }
    milliseconds = duration(details.get("lengthSeconds"), long_video=long_video)
    meta["duration_ms"] = milliseconds
    tracks = (
        info.get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
    )
    if not isinstance(tracks, list) or not tracks:
        return {**meta, "reason": "intake_subtitles_unavailable"}, None
    tracks = sorted(
        (t for t in tracks[:100] if isinstance(t, dict)),
        key=lambda t: (not str(t.get("languageCode", "")).startswith("zh"), t.get("kind") == "asr"),
    )
    if not tracks:
        return {**meta, "reason": "intake_subtitles_unavailable"}, None
    track = tracks[0]
    link = urlsplit(track.get("baseUrl", ""))
    query = parse_qs(link.query)
    if (
        link.scheme != "https"
        or link.hostname not in {"youtube.com", "www.youtube.com"}
        or link.path != "/api/timedtext"
        or query.get("v") != [identity]
    ):
        raise InvalidInput("intake_subtitle_binding_invalid")
    query["fmt"] = ["vtt"]
    source = urlunsplit((link.scheme, link.netloc, link.path, urlencode(query, doseq=True), ""))
    result = fetch(
        source,
        1024 * 1024 if long_video else media_subtitles.MAX_SUBTITLE_BYTES,
        auxiliary={"www.youtube.com", "youtube.com"},
    )
    cues = media_subtitles.parse(result.data, "vtt", milliseconds, long_video=long_video)
    return {
        **meta,
        "language": str(track.get("languageCode", ""))[:100],
        "subtitle_kind": "platform_automatic" if track.get("kind") == "asr" else "published",
        "subtitle_sha256": digest(result.data),
        "cues": cues,
        "gaps": media_subtitles.gaps(cues, milliseconds),
    }, media_subtitles.normalized(cues)


def bilibili(
    url: str, page: str, fetch: Callable[..., article_fetch.Response], *, long_video: bool = False
) -> tuple[dict[str, Any], bytes | None]:
    parsed = urlsplit(url)
    match = re.fullmatch(r"/video/(BV[A-Za-z0-9]{10})/?", parsed.path)
    if match is None or parse_qs(parsed.query).get("p", ["1"]) != ["1"]:
        raise InvalidInput("intake_video_single_resource_required")
    data = embedded(page, "__INITIAL_STATE__").get("videoData", {})
    if data.get("bvid") != match[1] or type(data.get("cid")) is not int:
        raise InvalidInput("intake_video_metadata_invalid")
    milliseconds = duration(data.get("duration"), long_video=long_video)
    meta = {
        "title": str(data.get("title", ""))[:1000],
        "description": str(data.get("desc", ""))[:16000],
        "duration_ms": milliseconds,
    }
    endpoint = "https://api.bilibili.com/x/player/wbi/v2?" + urlencode(
        {"bvid": match[1], "cid": data["cid"]}
    )
    result = fetch(endpoint, 1024 * 1024, auxiliary={"api.bilibili.com"})
    try:
        player = json.loads(result.data)
        tracks = player.get("data", {}).get("subtitle", {}).get("subtitles", [])
        if player.get("code") != 0 or player.get("data", {}).get("need_login_subtitle"):
            return {**meta, "reason": "intake_subtitles_access_restricted"}, None
        if not tracks:
            return {**meta, "reason": "intake_subtitles_unavailable"}, None
        track = next((t for t in tracks[:100] if t.get("lan", "").startswith("zh")), tracks[0])
        url = track["subtitle_url"]
        url = "https:" + url if url.startswith("//") else url
        if not urlsplit(url).path.startswith("/bfs/subtitle/"):
            raise InvalidInput("intake_subtitle_binding_invalid")
        raw = fetch(
            url,
            1024 * 1024 if long_video else media_subtitles.MAX_SUBTITLE_BYTES,
            auxiliary={"aisubtitle.hdslb.com", "i0.hdslb.com", "i1.hdslb.com", "i2.hdslb.com"},
        )
        body = json.loads(raw.data)["body"]
        if not isinstance(body, list) or len(body) > (20000 if long_video else 2000):
            raise InvalidInput("intake_subtitle_invalid")
        cues = [
            {
                "start_ms": round(float(c["from"]) * 1000),
                "end_ms": round(float(c["to"]) * 1000),
                "text": c["content"],
            }
            for c in body
        ]
        normalized = media_subtitles.normalized(cues)
        verified = media_subtitles.parse(normalized, "srt", milliseconds, long_video=long_video)
        return {
            **meta,
            "language": track["lan"],
            "subtitle_kind": "published",
            "subtitle_sha256": digest(raw.data),
            "cues": verified,
            "gaps": media_subtitles.gaps(verified, milliseconds),
        }, normalized
    except (KeyError, TypeError, ValueError, OverflowError):
        raise InvalidInput("intake_subtitle_invalid") from None


def extract(
    url: str,
    response: article_fetch.Response,
    base: dict[str, Any],
    fetch: Callable[..., article_fetch.Response],
    *,
    long_video: bool = False,
) -> Content:
    if len(response.data) > MAX_PAGE:
        raise InvalidInput("intake_video_page_over_limit")
    page = response.data.decode("utf-8", errors="replace")
    site = base["site"]
    meta: dict[str, Any]
    if site == "wechat_channels":
        titles = Titles()
        titles.feed(page)
        meta, subtitle = (
            {"title": titles.title, "reason": "intake_channels_content_unavailable"},
            None,
        )
    else:
        meta, subtitle = (youtube if site == "youtube" else bilibili)(
            url, page, fetch, long_video=long_video
        )
    meta = {
        **base,
        **meta,
        "kind": "video",
        "content_state": "review_required" if subtitle else "metadata_only",
        "asr_called": False,
        "ocr": False,
        "media_downloaded": False,
        "visual_coverage": "not_analyzed",
        "reason": meta.get("reason")
        or ("intake_subtitle_coverage_gaps" if meta.get("gaps") else None),
    }
    provenance = canonical(meta).encode()
    note = (
        "# "
        + meta.get("title", "视频来源")
        + "\n\n来源："
        + url
        + "\n\n"
        + meta.get("description", "")
        + "\n\n"
        + (
            "\n\n".join(
                f"[{c['start_ms']}–{c['end_ms']} ms] {c['text']}" for c in meta.get("cues", [])
            )
            if subtitle
            else "正文/字幕待补全。"
        )
    )
    assets = (
        AssetInput(response.data, "source.html", "text/html"),
        AssetInput(provenance, "provenance.json", "application/json"),
    )
    files = {"note.md": note.encode(), "provenance.json": provenance}
    if subtitle:
        files["captions.srt"] = subtitle
    return Content(meta, assets, files)
