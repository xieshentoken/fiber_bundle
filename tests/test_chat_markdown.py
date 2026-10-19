import io
import zipfile

from fiber_bundle import chat_archive, chat_markdown

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic image"


def zipped(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    return result.getvalue()


def test_actual_export_shape_with_images_and_file_links_preserves_order():
    raw = zipped(
        {
            "聊天记录.txt": (
                "甲：先看图片\n[图片] files/图.jpg\n乙：阅读附件\n[文件] files/报告.pdf\n原文结束"
            ),
            "files/图.jpg": PNG,
            "files/报告.pdf": b"%PDF-synthetic",
        }
    )
    assert chat_archive.inspect(raw)["text_members"][0]["path"] == "聊天记录.txt"
    value = chat_markdown.render(
        raw,
        "合成聊天",
        {
            "files/图.jpg": "Attachments/images/existing.jpg",
            "files/报告.pdf": "Attachments/originals/existing.pdf",
        },
    )
    note = value["files"]["note.md"].decode()
    assert "甲：先看图片\n![[Attachments/images/existing.jpg]]\n乙：阅读附件" in note
    assert "[[Attachments/originals/existing.pdf|报告.pdf]]\n原文结束" in note
    assert "![[Attachments/originals/" not in note
    assert value["gaps"] == [] and len(value["references"]) == 2
    assert list(value["files"]) == ["note.md"]
    assert "platform_completeness: not_verified" in note


def test_markdown_and_wiki_attachments_are_rewritten_at_their_original_position():
    raw = zipped(
        {
            "聊天记录.txt": "前\n![图片](img/a.png)\n[[docs/a.pdf|文档]]\n后",
            "img/a.png": PNG,
            "docs/a.pdf": b"%PDF",
        }
    )
    note = chat_markdown.render(
        raw, "合成", {"img/a.png": "images/a.png", "docs/a.pdf": "files/a.pdf"}
    )
    assert "前\n![[images/a.png]]\n[[files/a.pdf|文档]]\n后" in note["files"]["note.md"].decode()
    assert note["gaps"] == []


def test_ambiguous_missing_and_external_references_never_guess_or_fetch():
    raw = zipped(
        {
            "聊天记录.txt": "[图片] 图.png\n[文件] absent.pdf\n[图片] https://example.org/private.png",
            "a/图.png": PNG,
            "b/图.png": PNG + b"second",
        }
    )
    value = chat_markdown.render(
        raw, "合成", {"a/图.png": "images/a.png", "b/图.png": "images/b.png"}
    )
    assert value["gaps"] == ["chat_attachment_ambiguous", "chat_attachment_missing"]
    assert "附件待核对" in value["files"]["note.md"].decode()


def test_other_txt_is_an_attachment_instead_of_a_second_chat_transcript():
    raw = zipped({"聊天记录.txt": "资料\n[文件] files/说明.txt", "files/说明.txt": "附加文字"})
    assert len(chat_archive.inspect(raw)["text_members"]) == 1
    value = chat_markdown.render(raw, "合成", {"files/说明.txt": "files/说明.txt"})
    assert "[[files/说明.txt|说明.txt]]" in value["files"]["note.md"].decode()


def test_missing_images_keep_text_and_explicit_markers_without_broken_links():
    raw = zipped({"聊天记录.txt": "前文\n[图片] missing.jpg\n![另图](other.png)\n后文"})
    value = chat_markdown.render(raw, "缺图聊天", {})
    note = value["files"]["note.md"].decode()
    assert "前文\n**[图片缺失：missing.jpg]**\n**[图片缺失：other.png]**\n后文" in note
    assert "completeness_status: partial" in note and "![[" not in note
    assert value["gaps"] == [] and value["missing_images"] == ["missing.jpg", "other.png"]
