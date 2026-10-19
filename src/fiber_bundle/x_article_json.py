"""Decode bound X Article JSON results without evaluating the surrounding JavaScript."""

import base64
import binascii
import json
import re
from typing import Any
from urllib.parse import urlsplit

from . import x_article_content
from .vendor import article_parser as upstream

_RESULT = re.compile(
    r'\bkind\s*:\s*"Result\.Ok"\s*,\s*value\s*:\s*(' + upstream.JS_STRING_PATTERN + r")"
)
_STATUS = re.compile(r"/(?:[^/]+/status|i/web/status)/(\d+)/?")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise upstream.CaptureError("X Article JSON has duplicate fields")
        result[key] = value
    return result


def _identity(value: Any, kind: str) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = base64.b64decode(value, validate=True).decode("ascii")
    except (ValueError, UnicodeError, binascii.Error):
        return None
    prefix = kind + ":"
    return decoded[len(prefix) :] if decoded.startswith(prefix) else None


def _bound_article(payload: Any, target: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    value: Any = payload
    for key in ("data", "tweet_result_by_rest_id", "result"):
        value = value.get(key) if isinstance(value, dict) else None
    if not isinstance(value, dict) or value.get("__typename") != "Tweet":
        return None
    encoded = _identity(value.get("id"), "Tweet")
    rest = value.get("rest_id")
    if encoded is not None and rest is not None and encoded != rest:
        raise upstream.CaptureError("X Article tweet identities disagree")
    if (rest or encoded) != target:
        return None
    article = value.get("article")
    article = article.get("article_results") if isinstance(article, dict) else None
    article = article.get("result") if isinstance(article, dict) else None
    if not isinstance(article, dict) or article.get("__typename") != "ArticleEntity":
        return None
    article_id = _identity(article.get("id"), "ArticleEntity")
    if article_id is None or not article_id.isdigit():
        raise upstream.CaptureError("X Article JSON identity is missing")
    if article.get("rest_id", article_id) != article_id:
        raise upstream.CaptureError("X Article article identities disagree")
    return article


def extract(
    parser: upstream.DocumentParser, source_url: str, image_limit: int | None
) -> dict[str, object] | None:
    """Return only a complete result tied to the requested post; old formats stay upstream."""
    target = _STATUS.fullmatch(urlsplit(source_url).path)
    if target is None:
        return None
    articles: list[dict[str, Any]] = []
    for node in upstream.walk_nodes(parser.root):
        if node.tag != "script" or "data-tsr-stream-part" not in node.attrs:
            continue
        for match in _RESULT.finditer(upstream.raw_node_text(node)):
            token = match.group(1)
            if "ArticleEntity" not in token:
                continue
            try:
                payload = json.loads(
                    upstream.decode_js_string(token, "JSON result"), object_pairs_hook=_pairs
                )
            except (ValueError, RecursionError) as exc:
                raise upstream.CaptureError("X Article JSON result is malformed") from exc
            article = _bound_article(payload, target.group(1))
            if article is not None:
                articles.append(article)
    complete = [article for article in articles if "content_state" in article]
    if not complete:
        return None
    if not upstream.has_x_article_body(parser.root):
        raise upstream.CaptureError("X Article body marker is missing")
    # Preview and full responses must identify the same article, never a related post.
    if len({article["id"] for article in articles}) != 1:
        raise upstream.CaptureError("X Article JSON results disagree on article identity")
    canonical = {json.dumps(article, sort_keys=True, ensure_ascii=True) for article in complete}
    if len(canonical) != 1:
        raise upstream.CaptureError("X Article JSON has conflicting complete results")
    result = x_article_content.render(complete[0], parser.root, source_url, image_limit)
    social_title = upstream.metadata_value(parser, "og:title", "twitter:title")
    result.update(
        author=upstream.normalize_inline_text(re.sub(r"\s+on X$", "", social_title))[:180],
        published=upstream.normalize_inline_text(
            upstream.metadata_value(parser, "article:published_time", "date", "pubdate")
        )[:80],
    )
    return result
