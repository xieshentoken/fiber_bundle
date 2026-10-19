"""Validate and render every block and referenced image in an X JSON content_state."""

import re
from typing import Any, NoReturn

from .vendor import article_parser as up

_TEXT_TYPES = {
    "unstyled",
    "paragraph",
    "blockquote",
    "unordered-list-item",
    "ordered-list-item",
    "code-block",
    "header-one",
    "header-two",
    "header-three",
    "header-four",
    "header-five",
    "header-six",
}
_STYLES = {"BOLD": "**", "ITALIC": "*", "STRIKETHROUGH": "~~", "UNDERLINE": "<u>", "CODE": "`"}


def _fail(field: str) -> NoReturn:
    raise up.CaptureError("X Article JSON " + field + " is incomplete or unsupported")


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        _fail(field)
    assert isinstance(value, str)
    return value


def _image(media: Any) -> str:
    if not isinstance(media, dict):
        _fail("image")
    info = media.get("media_info")
    if not isinstance(info, dict) or info.get("__typename") != "ApiImage":
        _fail("image type")
    return up.normalize_public_https_url(_string(info.get("original_img_url"), "image URL"))


def _entities(value: Any) -> dict[int, dict[str, Any]]:
    if not isinstance(value, list) or len(value) > up.MAX_X_DRAFT_BLOCKS:
        _fail("entity map")
    result: dict[int, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            _fail("entity key")
        key = item["key"]
        entity = item.get("value")
        if not key.isascii() or not key.isdigit() or int(key) in result:
            _fail("entity key")
        if not isinstance(entity, dict) or not isinstance(entity.get("data"), dict):
            _fail("entity data")
        _string(entity.get("type"), "entity type")
        result[int(key)] = entity
    return result


def _range(value: Any, boundaries: dict[int, int]) -> tuple[int, int]:
    if not isinstance(value, dict):
        _fail("text range")
    offset, length = value.get("offset"), value.get("length")
    if type(offset) is not int or type(length) is not int or length < 1:
        _fail("text range")
    if offset not in boundaries or offset + length not in boundaries:
        _fail("text range")
    return boundaries[offset], boundaries[offset + length]


def _inline(block: dict[str, Any], entities: dict[int, dict[str, Any]]) -> str:
    text = _string(block.get("text"), "block text")
    boundaries, units = {0: 0}, 0
    for index, char in enumerate(text):
        units += 2 if ord(char) > 0xFFFF else 1
        boundaries[units] = index + 1
    styles, links = [], []
    for item in block.get("inline_style_ranges", []):
        start, end = _range(item, boundaries)
        style = _string(item.get("style"), "inline style").upper()
        if style not in _STYLES:
            _fail("inline style")
        styles.append((start, end, style))
    for item in block.get("entity_ranges", []):
        start, end = _range(item, boundaries)
        key = item.get("key")
        entity = entities.get(key) if type(key) is int else None
        if entity is None or entity["type"] != "LINK":
            _fail("inline entity")
        url = up.normalize_public_https_url(_string(entity["data"].get("url"), "link URL"))
        links.append((start, end, url))
    points = sorted({0, len(text), *(x for r in styles + links for x in r[:2])})
    parts = []
    for start, end in zip(points, points[1:], strict=False):
        segment = text[start:end]
        active = {style for a, b, style in styles if a <= start and end <= b}
        if "CODE" in active:
            fence = "`" * (
                1 + max((len(m.group()) for m in re.finditer(r"`+", segment)), default=0)
            )
            segment = fence + " " + segment + " " + fence
        else:
            segment = re.sub(r"([\\`*\[\]_<>])", r"\\\1", segment)
        for style in ("BOLD", "ITALIC", "STRIKETHROUGH", "UNDERLINE"):
            if style in active:
                marker = _STYLES[style]
                segment = marker + segment + ("</u>" if style == "UNDERLINE" else marker)
        targets = [url for a, b, url in links if a <= start and end <= b]
        if len(targets) > 1:
            _fail("overlapping links")
        if targets:
            segment = (
                "[" + segment + "](" + targets[0].replace("(", "%28").replace(")", "%29") + ")"
            )
        parts.append(segment)
    return "".join(parts)


def render(
    article: dict[str, Any], root: up.Node, source_url: str, image_limit: int | None
) -> dict[str, object]:
    title = _string(article.get("title"), "title").strip()
    state = article.get("content_state")
    if not title or not isinstance(state, dict):
        _fail("content state")
    blocks = state.get("blocks")
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= up.MAX_X_DRAFT_BLOCKS:
        _fail("block count")
    entities = _entities(state.get("entity_map"))
    media_urls = {}
    media_list = article.get("media_entities", [])
    if not isinstance(media_list, list):
        _fail("media list")
    for media in media_list:
        if not isinstance(media, dict):
            _fail("media item")
        key = _string(media.get("media_id"), "media identifier")
        if not key.isdigit() or key in media_urls:
            _fail("media identifier")
        media_urls[key] = _image(media)
    images: list[dict[str, object]] = []
    parts: list[str] = []

    def append_image(url: str) -> None:
        if image_limit is not None and len(images) >= image_limit:
            raise up.CaptureError("article_image_limit_exceeded")
        index = len(images) + 1
        images.append({"index": index, "url": url})
        parts.append("{{GEOF_IMAGE_" + str(index) + "}}")

    cover = article.get("cover_media_results")
    if cover is not None:
        if not isinstance(cover, dict):
            _fail("cover")
        append_image(_image(cover.get("result")))
    else:
        cover_url = up.x_article_cover_url(root, source_url)
        if cover_url:
            append_image(cover_url)
    keys: set[str] = set()
    source_text: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            _fail("block")
        key = _string(block.get("key"), "block key")
        text = _string(block.get("text"), "block text")
        kind = _string(block.get("type"), "block type")
        if not key or key in keys:
            _fail("block key")
        keys.add(key)
        source_text.append(text)
        for name in ("inline_style_ranges", "entity_ranges"):
            if not isinstance(block.get(name, []), list):
                _fail(name)
        depth = block.get("depth", 0)
        if type(depth) is not int or not 0 <= depth <= 8:
            _fail("block depth")
        if kind != "atomic":
            if kind not in _TEXT_TYPES:
                _fail("block type")
            if kind == "code-block":
                _inline(block, entities)  # Validate ranges even when code remains verbatim.
                rendered = up.render_x_text_block(kind, text)
            else:
                rendered = up.render_x_text_block(kind, _inline(block, entities))
            if rendered:
                parts.append(("  " * depth if kind.endswith("list-item") else "") + rendered)
            continue
        ranges = block.get("entity_ranges", [])
        if not ranges or block.get("inline_style_ranges"):
            _fail("atomic ranges")
        for item in ranges:
            boundaries = {0: 0}
            units = 0
            for index, char in enumerate(text):
                units += 2 if ord(char) > 0xFFFF else 1
                boundaries[units] = index + 1
            _range(item, boundaries)
            entity_key = item.get("key")
            entity = entities.get(entity_key) if type(entity_key) is int else None
            if entity is None:
                _fail("atomic entity")
            data, entity_type = entity["data"], entity["type"]
            if entity_type == "MEDIA":
                items = data.get("media_items")
                if items is None:
                    items = [data]
                if not isinstance(items, list) or not items:
                    _fail("media mapping")
                for media_item in items:
                    media_id = media_item.get("media_id") if isinstance(media_item, dict) else None
                    if not isinstance(media_id, str) or media_id not in media_urls:
                        _fail("referenced image")
                    append_image(media_urls[media_id])
            elif entity_type == "MARKDOWN":
                markdown = _string(data.get("markdown"), "markdown")
                if not markdown.strip():
                    _fail("markdown")
                parts.append(markdown)
            elif entity_type == "DIVIDER":
                parts.append("---")
            elif entity_type == "TWEET":
                tweet_id = _string(data.get("tweet_id"), "embedded tweet")
                if not tweet_id.isdigit():
                    _fail("embedded tweet")
                url = "https://x.com/i/web/status/" + tweet_id
                parts.append("> 嵌入推文：[" + url + "](" + url + ")")
            else:
                _fail("atomic entity type")
    if "plain_text" in article:
        plain = _string(article["plain_text"], "plain text")
        if up.normalize_inline_text(plain) != up.normalize_inline_text(" ".join(source_text)):
            _fail("plain text coverage")
    markdown = "\n\n".join(parts).strip()
    if len(up.normalize_inline_text(markdown)) < up.X_ARTICLE_MIN_CHARS:
        _fail("body length")
    return {
        "title": up.normalize_inline_text(title)[:180],
        "description": "",
        "markdown": markdown,
        "images": images,
        "math_count": 0,
        "article_marker": "x:article+content_state",
    }
