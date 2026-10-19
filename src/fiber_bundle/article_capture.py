"""Worker output contract and host verification, separate from network and publication."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .article_fetch import Response
from .articles import (
    MAX_HTML_BYTES,
    MAX_IMAGE_BYTES,
    MAX_TOTAL_IMAGE_BYTES,
    ArticleDraft,
    ArticleError,
    ImageBytes,
    complete_article,
    parse_article,
    public_url,
)
from .config import sync_directory


@dataclass(frozen=True)
class VerifiedCapture:
    draft: ArticleDraft
    raw_html: bytes
    content_type: str
    final_url: str


def write_new(root: Path, name: str, data: bytes) -> dict[str, object]:
    """Names are fixed by this module; partial writes remain for diagnosis."""
    fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(root)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def capture(url: str, root: Path, fetch: Callable[[str, int], Response]) -> None:
    """Only the fixed worker calls this; success manifest is written last."""
    if root.is_symlink() or root.resolve() != root or not root.is_dir() or any(root.iterdir()):
        raise ArticleError("capture_directory_not_empty_or_canonical")
    source_url = public_url(url)
    response = fetch(source_url, MAX_HTML_BYTES)
    source = write_new(root, "source.html", response.data)
    article = parse_article(response.data, source_url, response.content_type)
    entries = []
    images: dict[int, ImageBytes] = {}
    total = 0
    for image in article.images:
        fetched = fetch(image.url, MAX_IMAGE_BYTES)
        total += len(fetched.data)
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise ArticleError("image_total_limit")
        image_bytes = write_new(root, f"image-{image.index:03d}.bin", fetched.data)
        images[image.index] = ImageBytes(fetched.data, fetched.content_type)
        entries.append(
            {
                "index": image.index,
                "url": image.url,
                "content_type": fetched.content_type,
                **image_bytes,
            }
        )
    complete_article(article, images)
    manifest = {
        "schema": 1,
        "source_url": source_url,
        "final_url": response.url,
        "content_type": response.content_type,
        "source": source,
        "images": entries,
    }
    write_new(root, "capture.json", json.dumps(manifest, ensure_ascii=False).encode())


def read_bounded(root: Path, name: str, limit: int) -> bytes:
    fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= limit:
            raise ArticleError("invalid_capture_file")
        data = stream.read(limit + 1)
        if len(data) != info.st_size:
            raise ArticleError("capture_file_changed")
    return data


def _metadata(data: bytes, metadata: Any) -> None:
    if (
        not isinstance(metadata, dict)
        or type(metadata.get("bytes")) is not int
        or metadata["bytes"] != len(data)
        or metadata.get("sha256") != hashlib.sha256(data).hexdigest()
    ):
        raise ArticleError("capture_digest_mismatch")


def verify_capture(root: Path, expected_url: str) -> VerifiedCapture:
    """Run after the writer container stops; reparse HTML, never trust worker Markdown."""
    if root.is_symlink() or root.resolve() != root or not root.is_dir():
        raise ArticleError("invalid_capture_directory")
    try:
        manifest = json.loads(read_bounded(root, "capture.json", 65536))
        if (
            not isinstance(manifest, dict)
            or set(manifest)
            != {"schema", "source_url", "final_url", "content_type", "source", "images"}
            or type(manifest["schema"]) is not int
            or manifest["schema"] != 1
            or manifest["source_url"] != public_url(expected_url)
        ):
            raise ArticleError("capture_binding_mismatch")
        if not isinstance(manifest["final_url"], str):
            raise ArticleError("invalid_final_url")
        final_url = public_url(manifest["final_url"])
        content_type = manifest["content_type"]
        if not isinstance(content_type, str) or len(content_type) > 512:
            raise ArticleError("invalid_content_type")
        raw_html = read_bounded(root, "source.html", MAX_HTML_BYTES)
        _metadata(raw_html, manifest["source"])
        article = parse_article(raw_html, expected_url, content_type)
        entries = manifest["images"]
        if not isinstance(entries, list) or len(entries) != len(article.images):
            raise ArticleError("capture_image_count_mismatch")
        images: dict[int, ImageBytes] = {}
        total = 0
        for reference, entry in zip(article.images, entries, strict=True):
            if (
                not isinstance(entry, dict)
                or set(entry) != {"index", "url", "content_type", "sha256", "bytes"}
                or type(entry["index"]) is not int
                or entry["index"] != reference.index
                or entry["url"] != reference.url
                or not isinstance(entry["content_type"], str)
                or len(entry["content_type"]) > 512
            ):
                raise ArticleError("capture_image_binding_mismatch")
            data = read_bounded(root, f"image-{reference.index:03d}.bin", MAX_IMAGE_BYTES)
            total += len(data)
            if total > MAX_TOTAL_IMAGE_BYTES:
                raise ArticleError("image_total_limit")
            _metadata(data, entry)
            images[reference.index] = ImageBytes(data, entry["content_type"])
        expected_files = {"source.html", "capture.json"} | {f"image-{i:03d}.bin" for i in images}
        if {p.name for p in root.iterdir()} != expected_files:
            raise ArticleError("unexpected_capture_files")
        return VerifiedCapture(complete_article(article, images), raw_html, content_type, final_url)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, ArticleError):
            raise
        raise ArticleError("invalid_capture_artifacts") from exc
