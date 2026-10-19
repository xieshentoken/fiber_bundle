"""Domain-bound source selection. No network I/O and no URL substring permissions."""

import ipaddress
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..config import InvalidInput

HOSTS = {
    "x": {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "t.co"},
    "wechat": {"mp.weixin.qq.com"},
    "bilibili": {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"},
    "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
    "wechat_channels": {"channels.weixin.qq.com", "weixin.qq.com"},
}
URL = re.compile(r'https?://[^\s<>"\u3000]+', re.IGNORECASE)


def domain(value: str) -> str:
    if not isinstance(value, str) or len(value) > 253:
        raise ValueError("invalid intake domain")
    value = value.strip().lower().rstrip(".")
    try:
        value = value.encode("idna").decode("ascii")
        ipaddress.ip_address(value)
    except UnicodeError:
        raise ValueError("invalid intake domain") from None
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses are not website domains")
    labels = value.split(".")
    if (
        len(labels) < 2
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in labels)
        or labels[-1] in {"localhost", "local", "internal", "test", "invalid", "example"}
        or value in {"com.cn", "co.uk", "org.uk", "net.cn", "qq.com"}
        or labels[-1].isdigit()
    ):
        raise ValueError("invalid or overly broad intake domain")
    return value


def normalize(value: str) -> str:
    if not isinstance(value, str) or len(value) > 8192 or any(c.isspace() for c in value):
        raise InvalidInput("intake_link_invalid")
    try:
        u = urlsplit(value)
        host = domain(u.hostname or "")
        port = u.port
    except (ValueError, UnicodeError):
        raise InvalidInput("intake_link_invalid") from None
    if u.scheme not in {"https", "http"} or u.username or u.password or port not in {None, 443}:
        raise InvalidInput("intake_link_invalid")
    if u.scheme == "http" or port not in {None, 443}:
        raise InvalidInput("intake_link_https_required")
    return urlunsplit(("https", host, u.path or "/", u.query, ""))


def links(value: str) -> list[str]:
    found = []
    for match in URL.finditer(value):
        raw = match.group()
        while raw.endswith(tuple(".,;!?，。；！？，、]}>）")):
            raw = raw[:-1]
        while raw.endswith(")") and raw.count(")") > raw.count("("):
            raw = raw[:-1]
        if raw not in found:
            found.append(raw)
    return found


def site(value: str) -> str | None:
    u = urlsplit(normalize(value))
    for name, hosts in HOSTS.items():
        if u.hostname in hosts:
            if name == "wechat_channels" and u.hostname == "weixin.qq.com":
                return name if u.path.startswith("/sph/") else None
            return name
    return None


def allowed(value: str, settings: Mapping[str, Any]) -> bool:
    try:
        canonical = normalize(value)
        kind = site(canonical)
    except InvalidInput:
        return False
    if kind is not None:
        return kind in settings["sites"]
    host = urlsplit(canonical).hostname or ""
    # Custom entries are hosts, with only the conventional www alias. Other
    # subdomains must be selected explicitly; no implicit whole-domain crawl.
    return any(host in {d, "www." + d} for d in settings["custom_domains"])


def checked_redirects(chain: list[str], settings: Mapping[str, Any]) -> str:
    if not 1 <= len(chain) <= 6 or any(not allowed(url, settings) for url in chain):
        raise InvalidInput("intake_redirect_not_allowed")
    final = normalize(chain[-1])
    if urlsplit(final).hostname in {"t.co", "b23.tv", "youtu.be"}:
        raise InvalidInput("intake_short_link_unresolved")
    return final
