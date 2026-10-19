"""X code payloads preserve JS escapes and braces without executing source text."""

import json

import pytest
from test_articles import TEXT, X_URL, current_html

from fiber_bundle import articles
from fiber_bundle.vendor import article_parser as parser


def code_page(code):
    token = json.dumps(code).replace("<", r"\x3C")
    return current_html().replace(
        b'data:$R[15]={media_id:"9"},type:"MEDIA"',
        ("data:$R[15]={markdown:" + token + '},type:"MARKDOWN"').encode(),
    )


def test_nested_code_and_hex_escapes_retain_complete_source_order():
    code = '```js\nconst c = { nested: { x: "<tag>" } };\nconst raw = "\\x3C";\n```'
    article = articles.parse_article(code_page(code), X_URL)
    assert code in article.markdown
    assert article.markdown.index(TEXT) < article.markdown.index(code)
    assert article.markdown.index(code) < article.markdown.index("图片之后的段落")
    assert not article.images
    assert articles.complete_article(article, {}).markdown


@pytest.mark.parametrize(
    "token,expected",
    [
        (r'"\x3C"', "<"),
        (r'"\\x3C"', r"\x3C"),
        (r'"\\\x3C"', "\\<"),
        ('"source: (function(){return 1})()"', "source: (function(){return 1})()"),
    ],
)
def test_only_real_js_hex_escapes_are_decoded(token, expected):
    assert parser.decode_js_string(token, "markdown") == expected


@pytest.mark.parametrize("token", [r'"\xZZ"', r'"\x3"', r'"\q"', '"unterminated'])
def test_malformed_escapes_are_rejected(token):
    with pytest.raises(parser.CaptureError):
        parser.decode_js_string(token, "markdown")


def test_malformed_code_entity_never_falls_back_to_partial_article():
    raw = code_page("```js\n{ nested: {} }\n```").replace(b'type:"MARKDOWN"', b"type:invalid")
    with pytest.raises(articles.ArticleError) as error:
        articles.parse_article(raw, X_URL)
    assert error.value.code == "intake_x_article_serialization_invalid"
