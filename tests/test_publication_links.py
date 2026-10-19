"""Byte provenance follows only the controlled final link projection."""

from fiber_bundle import publication_links


def test_utf8_bom_and_image_projection_preserve_source_unit_boundaries():
    text = "\ufeff# 中文标题\n\n前段\n![说明](images/a.png)\n后段\n"
    edits = []
    projected = publication_links.images(
        text,
        {"images/a.png": "images/stable-图.png"},
        "Inbox/中文标题.md",
        "obsidian",
        edits_out=edits,
    )
    old_start = len(text[: text.index("后段")].encode())
    units = [{"id": "after", "rendered_start": old_start, "rendered_end": len(text.encode())}]
    moved = publication_links.byte_spans(text, edits, units)
    assert projected.startswith("\ufeff") and "中文标题" in projected
    assert "说明" in projected and "![[images/stable-图.png]]" in projected
    assert (
        projected.encode()[moved[0]["rendered_start"] : moved[0]["rendered_end"]]
        == "后段\n".encode()
    )


def test_boundary_inside_replaced_link_is_explicitly_unknown():
    text = "![](images/a.png)"
    edits = []
    publication_links.images(
        text, {"images/a.png": "images/b.png"}, "Inbox/A.md", "obsidian", edits_out=edits
    )
    assert (
        publication_links.byte_spans(text, edits, [{"rendered_start": 2, "rendered_end": 10}])
        is None
    )
