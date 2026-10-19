"""Canonical Bilibili/YouTube identifiers; never accept secrets, signed media URLs or playlists."""

import re
from urllib.parse import parse_qs, urlsplit

from .config import InvalidInput


def source_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 300 or any(c.isspace() for c in value):
        raise InvalidInput("media_link_url")
    try:
        u = urlsplit(value)
        port = u.port
        q = parse_qs(u.query, strict_parsing=True) if u.query else {}
    except ValueError:
        raise InvalidInput("media_link_url") from None
    if u.scheme != "https" or u.username or u.password or port or u.fragment:
        raise InvalidInput("media_link_url")
    if u.hostname in ("www.bilibili.com", "bilibili.com") and not u.query:
        m = re.fullmatch(r"/video/(BV[A-Za-z0-9]{10})/?", u.path)
        if m:
            return "https://www.bilibili.com/video/" + m[1]
    if u.hostname in ("www.youtube.com", "youtube.com") and u.path == "/watch":
        if set(q) == {"v"} and len(q["v"]) == 1 and re.fullmatch(r"[A-Za-z0-9_-]{11}", q["v"][0]):
            return "https://www.youtube.com/watch?v=" + q["v"][0]
    if u.hostname == "youtu.be" and not u.query and re.fullmatch(r"/[A-Za-z0-9_-]{11}", u.path):
        return "https://www.youtube.com/watch?v=" + u.path[1:]
    raise InvalidInput("media_link_platform_matrix")
