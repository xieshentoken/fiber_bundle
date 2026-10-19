"""Bounded literal SRT/VTT parsing; never infer speech or correct source words."""

import re
from typing import Any

from .config import InvalidInput
from .media_contract import MAX_DURATION_MS, MAX_RANGES

MAX_SUBTITLE_BYTES = 64 * 1024


def timestamp(value: str, kind: str) -> int:
    pattern = (
        r"(\d{2}):(\d{2}):(\d{2})[,](\d{3})"
        if kind == "srt"
        else (r"(?:(\d{2}):)?(\d{2}):(\d{2})[.](\d{3})")
    )
    match = re.fullmatch(pattern, value)
    if match is None:
        raise InvalidInput("media_subtitle_timestamp")
    hours, minutes, seconds, millis = match.groups()
    if int(minutes) >= 60 or int(seconds) >= 60:
        raise InvalidInput("media_subtitle_timestamp")
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def parse(
    raw: bytes, kind: str, duration_ms: int, *, long_video: bool = False
) -> list[dict[str, Any]]:
    if kind not in {"srt", "vtt"} or not 0 < len(raw) <= (
        1024 * 1024 if long_video else MAX_SUBTITLE_BYTES
    ):
        raise InvalidInput("media_subtitle_format_or_size")
    if (
        type(duration_ms) is not int
        or duration_ms < 1
        or (not long_video and duration_ms > MAX_DURATION_MS)
    ):
        raise InvalidInput("media_duration")
    try:
        value = raw.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeError as exc:
        raise InvalidInput("media_subtitle_encoding") from exc
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise InvalidInput("media_subtitle_control")
    if kind == "vtt":
        header, separator, value = value.partition("\n\n")
        if header != "WEBVTT" or not separator:
            raise InvalidInput("media_subtitle_header")
    blocks = value.strip("\n").split("\n\n")
    if not 1 <= len(blocks) <= (20000 if long_video else MAX_RANGES):
        raise InvalidInput("media_subtitle_cues")
    result = []
    previous = -1
    for index, block in enumerate(blocks, 1):
        lines = block.split("\n")
        if kind == "srt":
            if not lines or lines.pop(0) != str(index):
                raise InvalidInput("media_subtitle_index")
        elif lines and " --> " not in lines[0]:
            identifier = lines.pop(0)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier):
                raise InvalidInput("media_subtitle_identifier")
        if len(lines) < 2 or lines[0].count(" --> ") != 1:
            raise InvalidInput("media_subtitle_cue")
        start, end = (timestamp(v, kind) for v in lines.pop(0).split(" --> "))
        literal = "\n".join(lines)
        if not 0 <= start < end <= duration_ms or start < previous or not literal.strip():
            raise InvalidInput("media_subtitle_range")
        # Cue settings, markup, metadata blocks and inline timestamp tags are outside this slice.
        if "<" in literal or ">" in literal or len(literal) > 4000:
            raise InvalidInput("media_subtitle_text")
        result.append({"start_ms": start, "end_ms": end, "text": literal})
        previous = start
    return result


def normalized(cues: list[dict[str, Any]]) -> bytes:
    def clock(value: int) -> str:
        seconds, millis = divmod(value, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"

    return (
        "\n\n".join(
            f"{i}\n{clock(c['start_ms'])} --> {clock(c['end_ms'])}\n{c['text']}"
            for i, c in enumerate(cues, 1)
        ).encode()
        + b"\n"
    )


def gaps(cues: list[dict[str, Any]], duration_ms: int) -> list[dict[str, int]]:
    position = 0
    result = []
    for cue in cues:
        if cue["start_ms"] > position:
            result.append({"start_ms": position, "end_ms": cue["start_ms"]})
        position = max(position, cue["end_ms"])
    if position < duration_ms:
        result.append({"start_ms": position, "end_ms": duration_ms})
    return result
