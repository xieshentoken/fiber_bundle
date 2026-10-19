import pytest

from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import note_moc_fields as fields


@pytest.mark.parametrize("newline,bom", [("\n", ""), ("\r\n", "\ufeff")])
def test_add_field_preserves_all_other_bytes(newline, bom):
    text = bom + newline.join(
        [
            "---",
            "title: '标题' # keep",
            "tags:",
            "  - 传热",
            "custom: {x: 1}",
            "---",
            "",
            "正文 ![[图.png]]",
            "",
        ]
    )
    raw = text.encode()
    link = "[[Map/热管理 MOC]]"
    result, owned = fields.render(raw, [link], [])
    assert owned == [link]
    assert fields.field(result.decode())[2] == [link]
    assert result.replace(("moc:" + newline + '  - "' + link + '"' + newline).encode(), b"") == raw
    assert fields.render(result, [link], owned) == (result, owned)


@pytest.mark.parametrize(
    "value",
    [
        '"[[人工 MOC]]"',
        "'[[人工 MOC]]'",
        '["[[人工 MOC]]", "[[其他 MOC]]"]',
        "\n  - '[[人工 MOC]]'\n  - '[[其他 MOC]]'",
        "\n- '[[人工 MOC]]'",
    ],
)
def test_scalar_flow_and_block_keep_manual_values(value):
    raw = ("---\ntitle: 标题\nmoc: " + value + "\nauthor: 作者\n---\n正文\n").encode()
    link = "[[Map/热管理 MOC]]"
    result, owned = fields.render(raw, [link], [])
    assert fields.field(result.decode())[2][-1] == link
    assert "[[人工 MOC]]" in fields.field(result.decode())[2]
    assert b"author:" in result and result.endswith("---\n正文\n".encode())
    assert owned == [link]
    removed, owned = fields.render(result, [], owned)
    assert "[[人工 MOC]]" in fields.field(removed.decode())[2]
    assert link not in fields.field(removed.decode())[2]
    assert owned == []


def test_existing_manual_alias_is_not_claimed_or_duplicated():
    raw = b'---\nmoc: "[[Manual MOC|alias]]"\n---\nbody'

    def resolve(value):
        return fields.target(value).removeprefix("Map/")

    result, owned = fields.render(raw, ["[[Map/Manual MOC]]"], [], resolve)
    assert result == raw and owned == []


def test_no_frontmatter_and_bom_preserve_body():
    raw = "\ufeff# Title\r\n\r\n正文".encode()
    result, owned = fields.render(raw, ["[[Map/A]]"], [])
    assert result.decode().startswith('\ufeff---\r\nmoc:\r\n  - "[[Map/A]]"\r\n---\r\n# Title')
    assert fields.human_text(result.decode(), owned).endswith("# Title\r\n\r\n正文")


@pytest.mark.parametrize(
    "text",
    [
        "---\ntitle: x",
        "---\nmoc: a\nmoc: b\n---\nbody",
        "---\nmoc:\n  nested: unsupported\n---\nbody",
        "---\nmoc: &shared [a]\n---\nbody",
    ],
)
def test_invalid_or_unsupported_field_fails_without_output(text):
    with pytest.raises(IntegrityError):
        fields.render(text.encode(), ["[[Map/A]]"], [])


def test_comments_and_quoted_commas_survive():
    raw = b"---\nmoc: [\"[[A,B]]\", '[[C]]'] # explanation\n# keep comment\ntitle: X\n---\nbody"
    result, owned = fields.render(raw, ["[[Map/D]]"], [])
    assert fields.field(result.decode())[2] == ["[[A,B]]", "[[C]]", "[[Map/D]]"]
    assert b"# explanation" in result and b"# keep comment" in result
    assert owned == ["[[Map/D]]"]
