"""Typed article extraction boundary; no network, model, Vault or database access.

The fixed upstream parser owns format decoding. This module validates its output
and only produces a portable draft when every declared image has been supplied.
A draft is not a publication; authorization and durable publication belong to the
host pipeline.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import article_dates, x_article_json
from .vendor import article_parser as upstream
from .vendor import wechat_image_message

MAX_HTML_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 80 * 1024 * 1024
MAX_IMAGES = upstream.MAX_IMAGES
IMAGE_MARKER = re.compile(r"\{\{GEOF_(HTML_)?IMAGE_([1-9][0-9]*)\}\}")


class ArticleError(ValueError):
    """An incomplete or unsupported article; the input must remain unpublished."""

    def __init__(self, message: str, *, code: str = "intake_content_incomplete") -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ImageReference:
    index: int
    url: str


@dataclass(frozen=True)
class Article:
    source_url: str
    source_sha256: str
    title: str
    author: str
    published: str
    markdown: str
    marker: str
    images: tuple[ImageReference, ...]
    date_evidence: dict[str, object] | None = None


@dataclass(frozen=True)
class ImageBytes:
    data: bytes
    content_type: str


@dataclass(frozen=True)
class DraftImage:
    index: int
    source_url: str
    relative_path: str
    media_type: str
    sha256: str
    data: bytes


@dataclass(frozen=True)
class ArticleDraft:
    article: Article
    markdown: str
    images: tuple[DraftImage, ...]


def public_url(value: str) -> str:
    """Validate URL syntax; the network boundary must additionally check DNS/IPs."""
    try:
        return upstream.normalize_public_https_url(value)
    except (upstream.CaptureError, ValueError) as exc:
        raise ArticleError("invalid public HTTPS URL") from exc


def _text(result: Mapping[str, object], key: str, *, required: bool = False) -> str:
    value = result.get(key)
    if not isinstance(value, str) or (required and not value.strip()):
        raise ArticleError(f"invalid parser field: {key}")
    return value


def _extract_html(html: str, url: str, image_limit: int | None) -> dict[str, object]:
    if (urlsplit(url).hostname or "") in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        parser = upstream.DocumentParser()
        parser.feed(html)
        parser.close()
        result = x_article_json.extract(parser, url, image_limit)
        if result is not None:
            return result
    # Image-message pages have a swiper and serialized gallery, but no js_content.
    # Keep the fixed ordinary-article parser and its existing format contracts.
    if upstream.is_wechat_host(urlsplit(url).hostname or ""):
        parser = upstream.DocumentParser()
        parser.feed(html)
        parser.close()
        upstream.reject_wechat_access_challenge(url, html)
        if not upstream.wechat_article_marker(parser.root):
            marker = wechat_image_message.wechat_image_message_marker(parser.root, html)
            if marker:
                return wechat_image_message.extract_wechat_image_message(
                    parser, html, url, marker, image_limit=image_limit
                )
    return upstream.extract_article(html, url, image_limit=image_limit)


def parse_article(
    raw_html: bytes,
    source_url: str,
    content_type: str = "text/html; charset=utf-8",
    *,
    image_limit: int | None = MAX_IMAGES,
) -> Article:
    """Parse original HTML, retaining its digest and ordered image references.

    For X, a login page or hydrated DOM without serialization must never fall
    through to the upstream generic-webpage extractor.
    """
    if not raw_html or len(raw_html) > MAX_HTML_BYTES:
        raise ArticleError("HTML byte limit exceeded or empty input")
    if image_limit is not None and (type(image_limit) is not int or image_limit < 1):
        raise ArticleError("invalid image limit")
    url = public_url(source_url)
    host = urlsplit(url).hostname
    try:
        html = upstream.decode_html_document(raw_html, content_type)
        result = _extract_html(html, url, image_limit)
    except (upstream.CaptureError, RecursionError) as exc:
        if str(exc) == "article_image_limit_exceeded":
            raise ArticleError(
                "article image limit exceeded", code="intake_article_image_limit"
            ) from exc
        raise ArticleError(
            "article extraction incomplete or unsupported",
            code=(
                "intake_x_article_serialization_invalid"
                if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
                else "intake_content_incomplete"
            ),
        ) from exc
    marker = _text(result, "article_marker")
    if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"} and marker not in {
        "x:article+content_state",
        "x:article+draftjs",
    }:
        raise ArticleError("X Article serialization is required")
    markdown = _text(result, "markdown", required=True)
    references = result.get("images")
    if not isinstance(references, list) or (
        image_limit is not None and len(references) > image_limit
    ):
        raise ArticleError("invalid parser image list")
    images: list[ImageReference] = []
    for expected, reference in enumerate(references, 1):
        if not isinstance(reference, dict):
            raise ArticleError("invalid image reference")
        index, image_url = reference.get("index"), reference.get("url")
        if type(index) is not int or index != expected or not isinstance(image_url, str):
            raise ArticleError("invalid image order or URL")
        images.append(ImageReference(index, public_url(image_url)))
    expected_indexes = {image.index for image in images}
    actual_indexes = {int(match[2]) for match in IMAGE_MARKER.finditer(markdown)}
    if actual_indexes != expected_indexes:
        raise ArticleError("image placeholders and references do not match")
    return Article(
        url,
        hashlib.sha256(raw_html).hexdigest(),
        _text(result, "title", required=True),
        _text(result, "author"),
        _text(result, "published"),
        markdown,
        marker,
        tuple(images),
        article_dates.extract(html, url),
    )


def complete_article(article: Article, images: Mapping[int, ImageBytes]) -> ArticleDraft:
    """Resolve every image in place to deterministic, local relative paths.

    Missing, extra, oversized or unsupported image bytes reject the whole draft.
    The caller keeps raw HTML and successfully fetched bytes for diagnosis/retry.
    """
    if set(images) != {reference.index for reference in article.images}:
        raise ArticleError("image set is incomplete or contains unexpected images")
    total = 0
    resolved: dict[int, DraftImage] = {}
    for reference in article.images:
        image = images[reference.index]
        total += len(image.data)
        if not 128 <= len(image.data) <= MAX_IMAGE_BYTES or total > MAX_TOTAL_IMAGE_BYTES:
            raise ArticleError(f"image byte limit exceeded at index {reference.index}")
        try:
            mime, extension = upstream.sniff_image(image.data, image.content_type)
        except upstream.CaptureError as exc:
            raise ArticleError(f"invalid image at index {reference.index}") from exc
        declared = image.content_type.partition(";")[0].strip().lower()
        if declared.startswith("image/") and declared != mime:
            raise ArticleError(f"image MIME mismatch at index {reference.index}")
        digest = hashlib.sha256(image.data).hexdigest()
        resolved[reference.index] = DraftImage(
            reference.index,
            reference.url,
            f"images/{reference.index:03d}-{digest}.{extension.lstrip('.')}",
            mime,
            digest,
            image.data,
        )

    def replace(match: re.Match[str]) -> str:
        try:
            image = resolved[int(match[2])]
        except KeyError as exc:
            raise ArticleError("image placeholder has no asset") from exc
        if match[1]:
            return f'<img src="{image.relative_path}" alt="图片 {image.index}">'
        return f"![图片 {image.index}]({image.relative_path})"

    markdown = IMAGE_MARKER.sub(replace, article.markdown)
    return ArticleDraft(article, markdown, tuple(resolved.values()))
