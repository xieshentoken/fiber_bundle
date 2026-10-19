# Fixed extraction-only definitions from wechat-to-obsidian (Apache-2.0).
# Source and adaptation hashes are recorded in article-source.json.
# No publisher, network requests, sealed-task execution or Vault writes are imported.
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from .article_parser import (
    MAX_IMAGES, CaptureError, DocumentParser, MarkdownRenderer, Node,
    metadata_value, normalize_inline_text, normalize_public_https_url, walk_nodes,
)


def wechat_image_message_marker(root: Node, html_text: str) -> str:
    """Detect WeChat image-message pages (item_show_type 8, no js_content body)."""
    dom_marker = ""
    for node in walk_nodes(root):
        node_id = node.attrs.get("id", "").casefold()
        if node_id == "img_swiper":
            dom_marker = "id:img_swiper"
            break
        if node_id == "img_list":
            dom_marker = "id:img_list"
            break
    if not dom_marker:
        return ""
    if not re.search(r"picture_page_info_list\s*:\s*\[", html_text):
        return ""
    return f"{dom_marker}+picture_page_info_list"


def js_unescape(value: str) -> str:
    return re.sub(
        r"\\x([0-9a-fA-F]{2})|\\u([0-9a-fA-F]{4})",
        lambda match: chr(int(match.group(1) or match.group(2), 16)),
        value,
    )


def balanced_js_region(text: str, open_index: int) -> str:
    """Return the string-aware balanced {...} or [...] region starting at open_index."""
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for index in range(open_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in {"'", '"'}:
            in_string = True
            quote = char
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return text[open_index : index + 1]
    raise CaptureError("unbalanced JavaScript literal in the WeChat image message payload")


def js_array_items(array_region: str) -> list[str]:
    """Require every gallery entry to be an object; never skip an unmapped picture."""
    if not array_region.startswith("[") or not array_region.endswith("]"):
        raise CaptureError("invalid WeChat image message picture array")
    items: list[str] = []
    position = 1
    end = len(array_region) - 1
    while position < end:
        while position < end and array_region[position].isspace():
            position += 1
        if position == end:
            break
        if array_region[position] != "{":
            raise CaptureError("unmapped WeChat image message picture entry")
        item = balanced_js_region(array_region, position)
        if not item.endswith("}"):
            raise CaptureError("invalid WeChat image message picture object")
        items.append(item)
        position += len(item)
        while position < end and array_region[position].isspace():
            position += 1
        if position == end:
            break
        if array_region[position] != ",":
            raise CaptureError("invalid WeChat image message picture separator")
        position += 1
    return items


def js_object_member(region: str, name: str) -> str:
    """Return the first depth-1 single-quoted JS object member value, unescaped."""
    member_pattern = re.compile(
        r"(?<![\w$])" + re.escape(name) + r"\s*:\s*'((?:\\.|[^'\\])*)'"
    )
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    position = 0
    length = len(region)
    while position < length:
        char = region[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            position += 1
            continue
        if char in {"'", '"'}:
            in_string = True
            quote = char
            position += 1
            continue
        if char in "{[":
            depth += 1
            position += 1
            continue
        if char in "}]":
            depth -= 1
            position += 1
            continue
        if depth == 1 and (char.isalnum() or char in "_$"):
            match = member_pattern.match(region, position)
            if match:
                return js_unescape(match.group(1))
            while position < length and (
                region[position].isalnum() or region[position] in "_$"
            ):
                position += 1
            continue
        position += 1
    return ""


def wechat_image_message_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if value[:7].casefold() == "http://":
        host = (urlsplit(value).hostname or "").casefold()
        if host == "mmbiz.qpic.cn" or host.endswith(".qpic.cn"):
            value = "https://" + value[7:]
    return normalize_public_https_url(value)


def extract_wechat_image_message(
    parser: DocumentParser,
    html_text: str,
    source_url: str,
    marker: str,
    *, image_limit: int | None = MAX_IMAGES,
) -> dict:
    """Extract a WeChat image-message (贴图号) page from its cgiDataNew payload."""
    cgi_region = ""
    for match in re.finditer(r"cgiDataNew\s*=\s*\{", html_text):
        try:
            region = balanced_js_region(html_text, match.end() - 1)
        except CaptureError:
            continue
        if re.search(r"picture_page_info_list\s*:\s*\[", region):
            cgi_region = region
            break
    if not cgi_region:
        raise CaptureError("WeChat image message data payload is missing")
    title = normalize_inline_text(js_object_member(cgi_region, "title")) or metadata_value(
        parser, "og:title", "twitter:title"
    )
    if not title:
        raise CaptureError("WeChat image message title is missing")
    author = normalize_inline_text(js_object_member(cgi_region, "nick_name"))
    published = normalize_inline_text(js_object_member(cgi_region, "create_time"))
    caption_html = js_object_member(cgi_region, "content_noencode")
    if not caption_html:
        caption_html = html.unescape(js_object_member(cgi_region, "desc"))
    if not caption_html:
        caption_html = html.unescape(
            js_unescape(metadata_value(parser, "og:description", "description"))
        )
    images: list[dict[str, str | int]] = []
    for match in re.finditer(r"picture_page_info_list\s*:\s*\[", cgi_region):
        try:
            array_region = balanced_js_region(cgi_region, match.end() - 1)
            items = js_array_items(array_region)
        except CaptureError:
            continue
        if not items:
            continue
        if image_limit is not None and len(items) > image_limit:
            raise CaptureError("WeChat image message picture count exceeds the safety limit")
        for item in items:
            raw_url = js_object_member(item, "cdn_url")
            if not raw_url:
                raise CaptureError("WeChat image message picture URL is missing")
            try:
                url = wechat_image_message_url(raw_url)
            except CaptureError:
                raise CaptureError(
                    "WeChat image message picture URL is not a public HTTPS URL"
                ) from None
            images.append({"index": len(images) + 1, "url": url})
        break
    if not images:
        raise CaptureError("WeChat image message picture payload is missing or empty")
    gallery = "\n\n".join(
        part
        for index in range(1, len(images) + 1)
        for part in (f"**图{index}**", f"{{{{GEOF_IMAGE_{index}}}}}")
    )
    caption_markdown = ""
    math_count = 0
    if caption_html:
        prepared_caption = re.sub(r"\r\n?|\n", "<br>", caption_html)
        prepared_caption = re.sub(r"<a\b[^>]*>", "", prepared_caption)
        prepared_caption = re.sub(r"</a\s*>", "", prepared_caption)
        caption_parser = DocumentParser()
        caption_parser.feed(prepared_caption)
        caption_parser.close()
        renderer = MarkdownRenderer(source_url, images=images, image_limit=image_limit)
        caption_markdown, images, math_count = renderer.render(caption_parser.root)
        caption_markdown = re.sub(
            r"(?<=[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u2018-\u201f])"
            r" (?=[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u2018-\u201f])",
            "",
            caption_markdown,
        )
    markdown = f"{gallery}\n\n---\n\n{caption_markdown}" if caption_markdown else gallery
    return {
        "title": title[:180],
        "author": author[:180],
        "published": published[:80],
        "description": "",
        "markdown": markdown,
        "images": images,
        "math_count": math_count,
        "article_marker": f"wechat:image-message:{marker}",
    }
