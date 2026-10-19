"""Resolve shared video URLs before V3 initialization, without fetching media."""

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from . import article_fetch, media_link_contract
from .channels import intake_links
from .config import InvalidInput


def canonical(value: str) -> str:
    url = intake_links.normalize(value)
    parsed = urlsplit(url)
    if parsed.hostname in intake_links.HOSTS["bilibili"] - {"b23.tv"}:
        part = parse_qs(parsed.query).get("p", ["1"])
        if part != ["1"] or not re.fullmatch(r"/video/BV[A-Za-z0-9]{10}/?", parsed.path):
            raise InvalidInput("intake_video_single_resource_required")
        url = "https://www.bilibili.com" + parsed.path
    elif parsed.hostname in intake_links.HOSTS["youtube"]:
        query = parse_qs(parsed.query)
        if "list" in query:
            raise InvalidInput("intake_video_single_resource_required")
        if parsed.hostname == "youtu.be":
            url = "https://youtu.be" + parsed.path
        elif parsed.path == "/watch" and len(query.get("v", [])) == 1:
            url = "https://www.youtube.com/watch?v=" + query["v"][0]
        elif re.fullmatch(r"/(?:shorts|live|embed)/[A-Za-z0-9_-]{11}/?", parsed.path):
            url = "https://www.youtube.com/watch?v=" + parsed.path.rstrip("/").rsplit("/", 1)[1]
    return media_link_contract.source_url(url)


def resolve(url: str, guard: Callable[[], None]) -> dict[str, Any]:
    current = intake_links.normalize(url)
    chain = [current]
    for _ in range(6):
        guard()
        parsed = urlsplit(current)
        if parsed.hostname != "b23.tv":
            return {"original_url": url, "final_url": canonical(current), "redirects": chain}
        connection = article_fetch.PublicConnection("b23.tv", timeout=20)
        try:
            target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                target += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
            connection.request(
                "GET", target, headers={"User-Agent": "Mozilla/5.0", "Connection": "close"}
            )
            response = connection.getresponse()
            guard()
            location = response.getheader("Location")
            if response.status not in {301, 302, 303, 307, 308} or not location:
                raise InvalidInput("intake_video_short_link_unresolved")
            following = intake_links.normalize(urljoin(current, location))
            if intake_links.site(following) != "bilibili":
                raise InvalidInput("intake_redirect_not_allowed")
            if following in chain:
                raise InvalidInput("intake_video_short_link_unresolved")
            current = following
            chain.append(current)
        finally:
            connection.close()
    raise InvalidInput("intake_video_short_link_unresolved")
