"""Synthetic FiberMall layout; no external calls or real article content."""

import pytest

from fiber_bundle.articles import ArticleError, parse_article
from fiber_bundle.channels.intake_contract import Settings
from fiber_bundle.channels.intake_links import allowed, checked_redirects

URL = "https://www.fibermall.com/blog/thermal.htm"


def html():
    return (
        '<meta name="author" content="Test Author">'
        '<meta property="article:published_time" content="2022-11-26T08:31:43Z">'
        "<h1>Thermal article</h1><nav>Navigation</nav>"
        '<div data-widget_type="theme-post-content.default">'
        "<p>"
        + ("Synthetic thermal evidence. " * 15)
        + "</p><table><tr><th>Device</th><th>Temperature</th></tr>"
        "<tr><td>DSP</td><td>80</td></tr></table>"
        '<img data-src="/blog/wp-content/uploads/figure.jpg" src="data:image/png;base64,AA">'
        '<noscript><img src="/blog/wp-content/uploads/figure.jpg"></noscript>'
        '<div class="enb-related-product-theme-default">Unrelated product'
        '<img src="/product.png"></div>'
        '<div class="link-whisper-related-posts">Unrelated posts</div></div>'
    ).encode()


def test_fibermall_body_tables_lazy_image_and_metadata():
    article = parse_article(html(), URL)
    assert article.title == "Thermal article" and article.author == "Test Author"
    assert article.marker == "fibermall:theme-post-content"
    assert "DSP" in article.markdown and "80" in article.markdown
    assert len(article.images) == 1 and article.images[0].url.endswith("/figure.jpg")
    assert "Navigation" not in article.markdown and "Unrelated" not in article.markdown


@pytest.mark.parametrize(
    "value",
    [
        b"<h1>Access denied</h1>",
        b'<h1>Title</h1><div data-widget_type="theme-post-content.default">short</div>',
    ],
)
def test_missing_body_fails_closed(value):
    with pytest.raises(ArticleError):
        parse_article(value, URL)


def test_fibermall_permission_is_explicit_and_redirects_stay_bounded():
    settings = Settings().model_dump()
    assert not allowed(URL, settings)
    settings["sites"].append("fibermall")
    assert Settings.model_validate(settings) and allowed(URL, settings)
    assert not allowed("https://fibermall.com.attacker.com/blog/x.htm", settings)
    assert not allowed("https://shop.fibermall.com/blog/x.htm", settings)
    assert checked_redirects([URL, URL.replace("www.", "")], settings)
    assert allowed(URL, Settings(custom_domains=["fibermall.com"]).model_dump())


def test_fibermall_acquisition_keeps_images_on_allowed_host(monkeypatch):
    from test_article_fetch import Reply, install
    from test_articles import PNG

    from fiber_bundle.channels import intake_fetch

    install(
        monkeypatch,
        [
            Reply(200, html(), [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    settings = Settings(sites=["fibermall"], fetch_articles=True, policy_version=2).model_dump()
    fetched = []
    result = intake_fetch.acquire(URL, settings, fetched.append, lambda: None)
    assert result.metadata["site"] == "fibermall" and result.metadata["image_count"] == 1
    assert "note.md" in result.files and len(fetched) == 2
