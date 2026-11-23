"""FiberMall blog body selection; reuse the bounded table and image renderer."""

from urllib.parse import urlsplit

from .vendor import article_parser as parser

HOSTS = frozenset({"fibermall.com", "www.fibermall.com"})
PROMOTIONS = frozenset(
    {
        "addtoany_share_save_container",
        "enb-related-product-theme-default",
        "link-whisper-related-posts",
    }
)


def body(node: parser.Node) -> parser.Node:
    return parser.Node(
        node.tag,
        dict(node.attrs),
        [
            body(child) if isinstance(child, parser.Node) else child
            for child in node.children
            if not isinstance(child, parser.Node)
            or not PROMOTIONS.intersection(parser.class_tokens(child))
        ],
    )


def extract(html: str, url: str, image_limit: int | None) -> dict[str, object]:
    address = urlsplit(url)
    if (
        address.hostname not in HOSTS
        or not address.path.startswith("/blog/")
        or not address.path.endswith(".htm")
    ):
        raise parser.CaptureError("FiberMall blog article URL required")
    document = parser.DocumentParser()
    document.feed(html)
    document.close()
    candidates = [
        node
        for node in parser.walk_nodes(document.root)
        if node.attrs.get("data-widget_type") == "theme-post-content.default"
    ]
    title = parser.first_node_text(document.root, lambda node: node.tag == "h1")
    if len(candidates) != 1 or not title:
        raise parser.CaptureError("FiberMall article body or title missing")
    markdown, images, math_count = parser.MarkdownRenderer(url, image_limit=image_limit).render(
        body(candidates[0])
    )
    if len(parser.normalize_inline_text(markdown)) < 200:
        raise parser.CaptureError("FiberMall article body incomplete")
    return {
        "title": title[:180],
        "author": parser.metadata_value(document, "author", "article:author")[:180],
        "published": parser.metadata_value(
            document, "article:published_time", "og:article:published_time"
        )[:80],
        "markdown": markdown,
        "images": images,
        "math_count": math_count,
        "article_marker": "fibermall:theme-post-content",
    }
