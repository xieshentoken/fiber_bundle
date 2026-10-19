"""Synthetic article fixtures; no network, model calls or real Vault writes."""

import hashlib
import json

import pytest

from fiber_bundle.articles import ArticleError, ImageBytes, complete_article, parse_article

X_URL = "https://x.com/synthetic/status/123456789"
IMAGE_URL = "https://pbs.twimg.com/media/synthetic.png"
TEXT = "这是用于验证正文顺序、图片位置和完整性检查的合成段落。" * 5
# Minimal PNG bytes for signature tests; these are not image-decoder acceptance tests.
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(128))


def current_html(*, missing_media=False):
    def block(text, kind, ranges=""):
        return (
            "$R[1]={data:$R[2]={},entity_ranges:$R[3]=["
            + ranges
            + '],inline_style_ranges:$R[4]=[],key:"a",text:'
            + json.dumps(text)
            + ",type:"
            + json.dumps(kind)
            + "}"
        )

    blocks = [
        block(TEXT, "unstyled"),
        block(" ", "atomic", "$R[5]={key:0,length:1,offset:0}"),
        block("图片之后的段落", "header-two"),
    ]
    stream = (
        '__typename:"ArticleEntity",content_state:$R[10]={blocks:$R[11]=['
        + ",".join(blocks)
        + '],entity_map:$R[12]=[$R[13]={key:"0",value:$R[14]={'
        + 'data:$R[15]={media_id:"9"},type:"MEDIA"}}]},'
        + "media_entities:$R[16]=["
        + (
            ""
            if missing_media
            else '$R[17]={id:"9",media_id:"9",' + f"original_img_url:{json.dumps(IMAGE_URL)}" + "}"
        )
        + '],title:"Synthetic X"}}}'
    )
    return ('<div class="x-article-body"></div><script>' + stream + "</script>").encode()


def legacy_html(*, missing_index=False):
    stream = (
        '__typename:"ArticleEntity",title:"Legacy X",'
        '__id:"a:content_state",__typename:"DraftJsContentState",'
        '"a:content_state:blocks:0":$R[1]={__id:"b",__typename:"DraftJsBlock",'
        'key:"b",text:' + json.dumps(TEXT) + ',type:"unstyled"},'
        '"a:content_state:blocks:'
        + ("2" if missing_index else "1")
        + '":$R[2]={__id:"c",__typename:"DraftJsBlock",'
        'key:"c",text:" ",type:"atomic",'
        '__typename:"DraftJsEntityRange",key:0},'
        '"a:content_state:entity_map:0":$R[3]={__id:"e",'
        '__typename:"DraftJsEntityMap",key:"0",'
        '__typename:"DraftJsEntity",type:"MEDIA",'
        '__typename:"ArticleMediaKey",media_id:"9"},'
        '__typename:"ApiMedia",media_id:"9",original_img_url:' + json.dumps(IMAGE_URL)
    )
    return ('<div class="x-article-body"></div><script>' + stream + "</script>").encode()


@pytest.mark.parametrize(
    "fixture,marker",
    [
        (current_html, "x:article+content_state"),
        (legacy_html, "x:article+draftjs"),
    ],
)
def test_x_serializations_and_image_assets(fixture, marker):
    raw = fixture()
    article = parse_article(raw, X_URL)
    assert article.marker == marker
    assert article.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert [(i.index, i.url) for i in article.images] == [(1, IMAGE_URL)]
    draft = complete_article(article, {1: ImageBytes(PNG, "image/png")})
    assert draft.images[0].data == PNG
    assert draft.images[0].sha256 == hashlib.sha256(PNG).hexdigest()
    assert draft.markdown.index(TEXT) < draft.markdown.index("![图片 1]")
    assert "{{GEOF_" not in draft.markdown
    assert draft == complete_article(article, {1: ImageBytes(PNG, "image/png")})


def test_current_image_stays_between_text_blocks():
    article = parse_article(current_html(), X_URL)
    draft = complete_article(article, {1: ImageBytes(PNG, "image/png")})
    assert draft.markdown.index("![图片 1]") < draft.markdown.index("## 图片之后的段落")


@pytest.mark.parametrize(
    "raw",
    [
        ("<title>Log in</title><article>" + TEXT + "</article>").encode(),
        ('<div class="x-article-body">' + TEXT + "</div>").encode(),
        current_html(missing_media=True),
        legacy_html(missing_index=True),
        current_html().replace(b'key:"a",text:', b'unknown:true,key:"a",text:', 1),
        current_html().replace(b'<div class="x-article-body"></div>', b""),
    ],
)
def test_incomplete_x_refuses_fallback(raw):
    with pytest.raises(ArticleError):
        parse_article(raw, X_URL)


@pytest.mark.parametrize(
    "images",
    [
        {},
        {1: ImageBytes(PNG, "image/png"), 2: ImageBytes(PNG, "image/png")},
        {1: ImageBytes(b"<html>access denied</html>" * 10, "text/html")},
        {1: ImageBytes(b"\x89PNG\r\n\x1a\n", "image/png")},
        {1: ImageBytes(PNG, "image/jpeg")},
        {1: ImageBytes(PNG, "image/svg+xml")},
    ],
)
def test_missing_or_invalid_image_rejects_whole_draft(images):
    article = parse_article(current_html(), X_URL)
    with pytest.raises(ArticleError):
        complete_article(article, images)


def test_image_size_limits(monkeypatch):
    article = parse_article(current_html(), X_URL)
    monkeypatch.setattr("fiber_bundle.articles.MAX_TOTAL_IMAGE_BYTES", len(PNG) - 1)
    with pytest.raises(ArticleError, match="byte limit"):
        complete_article(article, {1: ImageBytes(PNG, "image/png")})


def test_wechat_retains_table_code_and_image_positions():
    raw = (
        '<h1 id="activity-name">Synthetic WeChat</h1><div id="js_content"><p>'
        + TEXT
        + "</p><table><tr><th>A</th><th>B</th></tr>"
        "<tr><td>first</td><td>second</td></tr></table>"
        "<pre><code>if a &lt; b:\n    pass</code></pre>"
        '<img data-src="https://mmbiz.qpic.cn/synthetic.png"><p>结束段落</p></div>'
    ).encode()
    article = parse_article(raw, "https://mp.weixin.qq.com/s/synthetic")
    draft = complete_article(article, {1: ImageBytes(PNG, "image/png")})
    assert "first" in draft.markdown and "second" in draft.markdown
    assert "if a < b:" in draft.markdown
    assert draft.markdown.index("![图片 1]") < draft.markdown.index("结束段落")


@pytest.mark.parametrize("url", ["http://x.com/a", "https://127.0.0.1/a", "https://u:p@x.com/a"])
def test_unsafe_source_url(url):
    with pytest.raises(ArticleError):
        parse_article(current_html(), url)


def test_html_image_limit_does_not_silently_truncate():
    from fiber_bundle.articles import MAX_IMAGES

    raw = (
        '<h1 id="activity-name">Limit</h1><div id="js_content">'
        + TEXT
        + '<img src="https://mmbiz.qpic.cn/image.png">' * (MAX_IMAGES + 1)
        + "</div>"
    ).encode()
    with pytest.raises(ArticleError, match="image limit") as error:
        parse_article(raw, "https://mp.weixin.qq.com/s/synthetic")
    assert error.value.code == "intake_article_image_limit"


@pytest.mark.parametrize("count", [60, 61, 86, 256, 512])
def test_long_wechat_article_keeps_every_image_position(count):
    raw = (
        '<h1 id="activity-name">Long article</h1><div id="js_content">'
        + TEXT
        + "".join(f'<p>段落{i}</p><img src="https://mmbiz.qpic.cn/{i}.png">' for i in range(count))
        + "<p>末段完整保留</p></div>"
    ).encode()
    article = parse_article(raw, "https://mp.weixin.qq.com/s/synthetic", image_limit=None)
    assert len(article.images) == count
    draft = complete_article(
        article, {i: ImageBytes(PNG, "image/png") for i in range(1, count + 1)}
    )
    assert len(draft.images) == count
    assert draft.markdown.index(f"![图片 {count}]") < draft.markdown.index("末段完整保留")


def test_image_inside_merged_table_cell_remains_html():
    raw = (
        '<h1 id="activity-name">Table</h1><div id="js_content">'
        + TEXT
        + '<table><tr><td colspan="2"><img src="https://mmbiz.qpic.cn/image.png">'
        "</td></tr><tr><td>A</td><td>B</td></tr></table></div>"
    ).encode()
    article = parse_article(raw, "https://mp.weixin.qq.com/s/synthetic")
    draft = complete_article(article, {1: ImageBytes(PNG, "image/png")})
    assert '<img src="images/' in draft.markdown
    assert "<table>" in draft.markdown and 'colspan="2"' in draft.markdown
    assert "{{GEOF_" not in draft.markdown


def test_vendor_integrity():
    from importlib.resources import files

    source = files("fiber_bundle.vendor")
    provenance = json.loads(source.joinpath("article-source.json").read_text())
    assert (
        hashlib.sha256(source.joinpath("article_parser.py").read_bytes()).hexdigest()
        == (provenance["adapted_sha256"])
    )
