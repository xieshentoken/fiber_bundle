"""Synthetic nested X JSON; reject partial bodies and images without touching real Vaults."""

import base64
import copy
import json

import pytest
from test_articles import IMAGE_URL, PNG, TEXT, X_URL

from fiber_bundle.articles import ArticleError, ImageBytes, complete_article, parse_article

TWEET = "123456789"
ARTICLE = "987654321"
COVER = "https://pbs.twimg.com/media/synthetic-cover.png"


def identity(kind, value):
    return base64.b64encode((kind + ":" + value).encode()).decode()


def block(key, text, kind="unstyled", **fields):
    return {
        "key": key,
        "text": text,
        "type": kind,
        "data": {},
        "inline_style_ranges": [],
        "entity_ranges": [],
        **fields,
    }


def image(identifier, url):
    return {
        "media_id": identifier,
        "media_info": {"__typename": "ApiImage", "original_img_url": url},
    }


def article():
    return {
        "__typename": "ArticleEntity",
        "id": identity("ArticleEntity", ARTICLE),
        "title": "Synthetic JSON article",
        "plain_text": TEXT + "   图片之后的段落",
        "content_state": {
            "blocks": [
                block("a", TEXT),
                block("b", " ", "atomic", entity_ranges=[{"key": 0, "offset": 0, "length": 1}]),
                block("c", "图片之后的段落", "header-two"),
            ],
            "entity_map": [
                {
                    "key": "0",
                    "value": {"type": "MEDIA", "data": {"media_items": [{"media_id": "9"}]}},
                }
            ],
        },
        "cover_media_results": {"result": image("8", COVER)},
        "media_entities": [image("9", IMAGE_URL)],
    }


def payload(value, tweet=TWEET, *, rest=True):
    result = {
        "__typename": "Tweet",
        "id": identity("Tweet", tweet),
        "article": {"article_results": {"result": value}},
    }
    if rest:
        result["rest_id"] = tweet
    return {"data": {"tweet_result_by_rest_id": {"result": result}}}


def page(*values, marker=True):
    # JSON response text is a JavaScript string, never an evaluated expression.
    scripts = "".join(
        '<script data-tsr-stream-part>$_TSR.router=($R=>$R[1]={kind:"Result.Ok",value:'
        + json.dumps(json.dumps(value, ensure_ascii=False))
        + "})</script>"
        for value in values
    )
    return (("<div class='x-article-body'></div>" if marker else "") + scripts).encode()


def test_json_preview_and_full_body_preserve_all_images_and_positions():
    full = article()
    preview = {
        "__typename": "ArticleEntity",
        "id": full["id"],
        "rest_id": ARTICLE,
        "title": full["title"],
    }
    raw = page(payload(preview), payload(full, rest=False))
    parsed = parse_article(raw, X_URL)
    assert parsed.marker == "x:article+content_state"
    assert [i.url for i in parsed.images] == [COVER, IMAGE_URL]
    draft = complete_article(
        parsed, {1: ImageBytes(PNG, "image/png"), 2: ImageBytes(PNG, "image/png")}
    )
    assert draft.markdown.index("![图片 1]") < draft.markdown.index(TEXT)
    assert (
        draft.markdown.index(TEXT)
        < draft.markdown.index("![图片 2]")
        < draft.markdown.index("图片之后的段落")
    )
    assert "{{GEOF_" not in draft.markdown


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_media",
        "missing_map",
        "duplicate_key",
        "unknown_block",
        "unknown_entity",
        "truncated_body",
        "conflicting_identity",
        "invalid_range",
        "invalid_style",
        "bad_cover",
        "duplicate_block",
    ],
)
def test_incomplete_json_never_publishes_partial_body(mutation):
    value = article()
    blocks = value["content_state"]["blocks"]
    if mutation == "missing_media":
        value["media_entities"] = []
    elif mutation == "missing_map":
        value["content_state"]["entity_map"] = []
    elif mutation == "duplicate_key":
        value["content_state"]["entity_map"] *= 2
    elif mutation == "unknown_block":
        blocks[0]["type"] = "unknown"
    elif mutation == "unknown_entity":
        value["content_state"]["entity_map"][0]["value"]["type"] = "VIDEO"
    elif mutation == "truncated_body":
        blocks.pop()
    elif mutation == "conflicting_identity":
        value["rest_id"] = "999"
    elif mutation == "invalid_range":
        blocks[1]["entity_ranges"][0]["offset"] = 99
    elif mutation == "invalid_style":
        blocks[0]["inline_style_ranges"] = [{"offset": 0, "length": 2, "style": "UNKNOWN"}]
    elif mutation == "bad_cover":
        value["cover_media_results"] = {"result": {"media_info": {"__typename": "ApiVideo"}}}
    elif mutation == "duplicate_block":
        blocks[2]["key"] = blocks[0]["key"]
    with pytest.raises(ArticleError) as error:
        parse_article(page(payload(value)), X_URL)
    assert error.value.code == "intake_x_article_serialization_invalid"


def test_wrong_post_unbound_article_and_missing_body_marker_are_rejected():
    for raw in [
        page(payload(article(), tweet="999")),
        page({"data": article()}),
        page(payload(article()), marker=False),
    ]:
        with pytest.raises(ArticleError):
            parse_article(raw, X_URL)


def test_mismatched_tweet_and_conflicting_full_or_preview_are_rejected():
    original = payload(article())
    mismatch = copy.deepcopy(original)
    mismatch["data"]["tweet_result_by_rest_id"]["result"]["rest_id"] = "999"
    changed = article()
    changed["title"] = "Conflicting"
    other = article()
    other["id"] = identity("ArticleEntity", "999")
    other.pop("content_state")
    for raw in [page(mismatch), page(original, payload(changed)), page(original, payload(other))]:
        with pytest.raises(ArticleError):
            parse_article(raw, X_URL)
    assert parse_article(page(original, original), X_URL).title == article()["title"]


def test_json_text_links_and_utf16_style_ranges_are_preserved():
    value = article()
    text = "😀 link " + TEXT
    value["content_state"]["blocks"] = [
        block(
            "a",
            text,
            inline_style_ranges=[{"offset": 3, "length": 4, "style": "Bold"}],
            entity_ranges=[{"offset": 3, "length": 4, "key": 0}],
        )
    ]
    value["plain_text"] = text
    value["content_state"]["entity_map"] = [
        {"key": "0", "value": {"type": "LINK", "data": {"url": "https://example.org/article"}}}
    ]
    parsed = parse_article(page(payload(value)), X_URL)
    assert "[**link**](https://example.org/article)" in parsed.markdown
    value["content_state"]["blocks"][0]["inline_style_ranges"][0]["offset"] = 1
    with pytest.raises(ArticleError):
        parse_article(page(payload(value)), X_URL)


def test_code_markdown_divider_and_embedded_tweet_are_not_evaluated():
    value = article()
    code = '```js\nconst c = {nested:{x:"<tag>"}};\n```'
    value["content_state"]["blocks"] = [
        block("a", TEXT),
        *[
            block(str(i), " ", "atomic", entity_ranges=[{"key": i, "offset": 0, "length": 1}])
            for i in range(3)
        ],
    ]
    value.pop("plain_text")
    value["content_state"]["entity_map"] = [
        {"key": str(i), "value": entity}
        for i, entity in enumerate(
            [
                {"type": "MARKDOWN", "data": {"markdown": code}},
                {"type": "DIVIDER", "data": {}},
                {"type": "TWEET", "data": {"tweet_id": "456"}},
            ]
        )
    ]
    parsed = parse_article(page(payload(value)), X_URL)
    assert code in parsed.markdown and "\n\n---\n\n" in parsed.markdown
    assert "https://x.com/i/web/status/456" in parsed.markdown


def test_json_image_limits_and_image_bytes_checks_remain_required():
    raw = page(payload(article()))
    with pytest.raises(ArticleError) as error:
        parse_article(raw, X_URL, image_limit=1)
    assert error.value.code == "intake_article_image_limit"
    parsed = parse_article(raw, X_URL, image_limit=None)
    for images in [
        {1: ImageBytes(PNG, "image/png")},
        {1: ImageBytes(PNG, "image/png"), 2: ImageBytes(b"<html>denied</html>", "text/html")},
    ]:
        with pytest.raises(ArticleError):
            complete_article(parsed, images)


def test_json_duplicate_fields_and_invalid_json_are_rejected():
    raw = page(payload(article())).decode()
    token = json.dumps(json.dumps(payload(article()), ensure_ascii=False))
    bad = token.replace('\\"title\\":', '\\"title\\":\\"duplicate\\",\\"title\\":', 1)
    assert token in raw
    with pytest.raises(ArticleError):
        parse_article(raw.replace(token, bad).encode(), X_URL)
    with pytest.raises(ArticleError):
        parse_article(page(payload(article()))[:-25], X_URL)
