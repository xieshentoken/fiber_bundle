import io
import zipfile

import pytest

from fiber_bundle.chat_archive import inspect
from fiber_bundle.config import InvalidInput


def zipped(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as z:
        for name, value in files.items():
            z.writestr(name, value)
    return result.getvalue()


def test_literal_wechat_export_is_recognized_without_invented_identity():
    view = inspect(zipped({"chat.txt": "微信聊天记录\n2026-10-12 10:30:00 合成甲\n测试资料\n"}))
    assert view["engine"] == "ordinary" and not view["semantic_workpack"]
    assert view["chat_identity"] == "unknown" and view["platform_completeness"] == "not_verified"
    assert view["text_members"][0]["timestamp_headers"] == 1


@pytest.mark.parametrize(
    "files",
    [
        {"workpack.json": "{}", "chat.txt": "微信聊天记录"},
        {"word/document.xml": "", "chat.txt": "微信聊天记录"},
        {"notes.txt": "ordinary unstructured text"},
        {"../chat.txt": "微信聊天记录"},
    ],
)
def test_workpack_office_unknown_and_traversal_are_not_wechat_exports(files):
    with pytest.raises(InvalidInput):
        inspect(zipped(files))
