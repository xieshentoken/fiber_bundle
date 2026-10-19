"""WeChat image-message fixtures exercise the shared, network-free article boundary."""

import hashlib
import json
from importlib.resources import files

import pytest
from test_articles import PNG

from fiber_bundle.articles import ArticleError, ImageBytes, complete_article, parse_article

URL = "https://mp.weixin.qq.com/s/synthetic-image-message"
IMAGE_URLS = ("https://mmbiz.qpic.cn/first.png", "https://mmbiz.qpic.cn/second.png")


def image_message_html(
    *,
    marker="img_swiper",
    pictures="{cdn_url:'https://mmbiz.qpic.cn/first.png'},"
    "{cdn_url:'https://mmbiz.qpic.cn/second.png'}",
    caption="<p>图1是入口，图2是出口。</p>",
    extra="",
):
    return (
        f'<meta property="og:title" content="备用标题"><div id="{marker}"></div>'
        "<script>window.cgiDataNew = {"
        "title:'合成贴图号',nick_name:'合成作者',create_time:'2026-10-10',"
        f"content_noencode:'{caption}',picture_page_info_list:[{pictures}]"
        "};</script>" + extra
    ).encode()


@pytest.mark.parametrize("marker", ["img_swiper", "img_list"])
def test_gallery_and_short_caption_are_separate_and_ordered(marker):
    raw = image_message_html(marker=marker)
    article = parse_article(raw, URL)
    assert article.marker == f"wechat:image-message:id:{marker}+picture_page_info_list"
    assert article.title == "合成贴图号" and article.author == "合成作者"
    assert article.published == "2026-10-10"
    assert article.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert tuple(image.url for image in article.images) == IMAGE_URLS
    draft = complete_article(article, {i: ImageBytes(PNG, "image/png") for i in (1, 2)})
    assert draft.markdown.index("**图1**") < draft.markdown.index("**图2**")
    assert draft.markdown.index("![图片 2]") < draft.markdown.index("---")
    assert draft.markdown.index("---") < draft.markdown.index("图1是入口,图2是出口。")
    assert len(draft.images) == 2 and "{{GEOF_" not in draft.markdown


def test_image_only_message_needs_no_text_or_ocr():
    article = parse_article(image_message_html(caption=""), URL)
    assert len(article.images) == 2 and "---" not in article.markdown


def test_escaped_payload_and_nested_decoy_preserve_main_picture():
    raw = image_message_html(
        pictures="{thumbnail:{cdn_url:'https://mmbiz.qpic.cn/thumbnail.png'},"
        r"cdn_url:'http\x3a//mmbiz.qpic.cn/main.png?x=1\x26y=2'}",
        caption=r"\u56fe1：<a href=\x22https://example.com\x22>说明</a>",
    )
    article = parse_article(raw, URL)
    assert article.images[0].url == "https://mmbiz.qpic.cn/main.png?x=1&y=2"
    assert "图1:说明" in article.markdown and "example.com" not in article.markdown


def test_duplicate_picture_positions_are_kept():
    article = parse_article(
        image_message_html(pictures="{cdn_url:'https://mmbiz.qpic.cn/repeated.png'}," * 2), URL
    )
    assert [i.index for i in article.images] == [1, 2]
    assert article.images[0].url == article.images[1].url


@pytest.mark.parametrize(
    "raw",
    [
        image_message_html(marker="unrelated"),
        image_message_html(pictures=""),
        image_message_html(pictures="{}"),
        image_message_html(pictures="{thumbnail:{cdn_url:'https://mmbiz.qpic.cn/a.png'}}"),
        image_message_html(pictures="{cdn_url:'https://127.0.0.1/a.png'}"),
        image_message_html(pictures="{cdn_url:'javascript:alert(1)'}"),
        image_message_html(pictures="{cdn_url:'http://unapproved.example/a.png'}"),
        image_message_html().replace(b"window.cgiDataNew =", b"window.other ="),
        image_message_html().replace(b"]};", b";"),
        image_message_html(extra="当前环境异常，完成验证后即可继续访问"),
    ],
)
def test_incomplete_or_challenge_page_never_becomes_a_draft(raw):
    with pytest.raises(ArticleError):
        parse_article(raw, URL)


def test_missing_gallery_image_rejects_entire_draft():
    article = parse_article(image_message_html(), URL)
    with pytest.raises(ArticleError, match="incomplete"):
        complete_article(article, {1: ImageBytes(PNG, "image/png")})


def test_gallery_limit_never_silently_truncates():
    with pytest.raises(ArticleError):
        parse_article(
            image_message_html(pictures="{cdn_url:'https://mmbiz.qpic.cn/a.png'}," * 61), URL
        )


def test_extension_provenance_is_bound_to_packaged_bytes():
    source = files("fiber_bundle.vendor")
    provenance = json.loads(source.joinpath("article-source.json").read_text())
    record = provenance["extensions"]["wechat_image_message.py"]
    assert (
        hashlib.sha256(source.joinpath("wechat_image_message.py").read_bytes()).hexdigest()
        == (record["adapted_sha256"])
    )
    assert "extract_wechat_image_message" in record["selected_definitions"]
    assert "execute" not in record["selected_definitions"]


@pytest.mark.parametrize("unmapped", ["null", "17", "'https://mmbiz.qpic.cn/unmapped.png'"])
def test_nonobject_picture_entry_cannot_be_silently_dropped(unmapped):
    raw = image_message_html(pictures="{cdn_url:'https://mmbiz.qpic.cn/first.png'}," + unmapped)
    with pytest.raises(ArticleError):
        parse_article(raw, URL)
