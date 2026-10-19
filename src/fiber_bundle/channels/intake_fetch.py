"""Finite literal webpage acquisition; source hosts and auxiliary CDNs are distinct."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .. import article_fetch, articles
from ..article_capture import VerifiedCapture
from ..article_publication import content_files
from ..config import InvalidInput
from ..inputs import AssetInput, canonical
from ..objects import digest
from . import intake_links


@dataclass(frozen=True)
class Content:
    metadata: dict[str, Any]
    assets: tuple[AssetInput, ...]
    files: dict[str, bytes]


def acquire(
    url: str, settings: dict[str, Any], reserve: Callable[[str], None], guard: Callable[[], None]
) -> Content:
    deadline = time.monotonic() + 180
    acquired: dict[str, article_fetch.Response] = {}

    def check() -> None:
        guard()
        if time.monotonic() > deadline:
            raise InvalidInput("intake_fetch_timeout")

    def fetch(
        value: str, limit: int, *, auxiliary: set[str] | None = None
    ) -> article_fetch.Response:
        check()
        if value in acquired:
            previous = acquired[value]
            if len(previous.data) > limit:
                raise InvalidInput("intake_resource_over_limit")
            return previous
        reserve(value)

        def validate(hop: str) -> None:
            check()
            if auxiliary is None:
                if not intake_links.allowed(hop, settings):
                    raise InvalidInput("intake_redirect_not_allowed")
            else:
                target = urlsplit(intake_links.normalize(hop))
                expected = urlsplit(value)
                if target.hostname not in auxiliary:
                    raise InvalidInput("intake_auxiliary_host_not_allowed")
                # Caption redirects may change signatures, never the resource
                # identity or endpoint. Image CDNs remain host-bound.
                if expected.path in {"/api/timedtext", "/x/player/wbi/v2"}:
                    keys = ("v",) if expected.path == "/api/timedtext" else ("bvid", "cid")
                    if target.path != expected.path or any(
                        parse_qs(target.query).get(key) != parse_qs(expected.query).get(key)
                        for key in keys
                    ):
                        raise InvalidInput("intake_subtitle_binding_invalid")
                elif expected.path.startswith("/bfs/subtitle/") and target.path != expected.path:
                    raise InvalidInput("intake_subtitle_binding_invalid")

        response = article_fetch.fetch_public(value, limit, validate=validate)
        check()
        if len(response.data) > limit:
            raise InvalidInput("intake_resource_over_limit")
        acquired[value] = response
        return response

    source = intake_links.normalize(url)
    response = fetch(source, articles.MAX_HTML_BYTES)
    chain = list(response.redirects) or [source, response.url]
    final = intake_links.checked_redirects(chain, settings)
    kind = intake_links.site(final)
    base = {
        "original_url": url,
        "normalized_url": source,
        "redirects": chain,
        "final_url": final,
        "source_sha256": digest(response.data),
        "site": kind,
    }
    if kind in {"x", "wechat"}:
        if not settings["fetch_articles"]:
            raise InvalidInput("intake_article_extraction_off")
        parsed = articles.parse_article(
            response.data,
            final,
            response.content_type,
            image_limit=None if settings.get("policy_version") == 2 else articles.MAX_IMAGES,
        )
        if kind == "x" and parsed.marker not in {"x:article+content_state", "x:article+draftjs"}:
            raise InvalidInput("intake_x_article_serialization_required")
        cdns = {"mmbiz.qpic.cn", "mmbiz.qlogo.cn"} if kind == "wechat" else {"pbs.twimg.com"}
        images = {}
        total = 0
        for reference in parsed.images:
            image = fetch(reference.url, articles.MAX_IMAGE_BYTES, auxiliary=cdns)
            total += len(image.data)
            if total > articles.MAX_TOTAL_IMAGE_BYTES:
                raise InvalidInput("intake_image_total_limit")
            images[reference.index] = articles.ImageBytes(image.data, image.content_type)
        draft = articles.complete_article(parsed, images)
        files = content_files(VerifiedCapture(draft, response.data, response.content_type, final))
        return Content(
            {
                **base,
                "title": parsed.title,
                "kind": "article",
                "content_state": "review_required",
                "image_count": len(images),
                "ocr": False,
            },
            (
                AssetInput(response.data, "source.html", "text/html"),
                AssetInput(canonical(base).encode(), "provenance.json", "application/json"),
            ),
            files,
        )
    if kind in {"bilibili", "youtube", "wechat_channels"}:
        from .intake_video import extract

        return extract(final, response, base, fetch, long_video=settings.get("policy_version") == 2)
    raise InvalidInput("intake_extractor_unavailable")
