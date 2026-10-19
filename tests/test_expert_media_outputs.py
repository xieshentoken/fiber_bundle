"""Observable expert output negotiation, per-item failure, export and single-attempt SDK."""

import base64
import copy
import io
import json
import sys
import zipfile
from types import SimpleNamespace

import pytest

from fiber_bundle import expert_feishu_render, expert_mcp
from fiber_bundle import expert_presentation as output
from fiber_bundle.channels import query
from fiber_bundle.objects import digest

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ZkAAAAASUVORK5CYII="
)


@pytest.fixture
def packet():
    return {
        "answer": "普通文字 ![private](https://example.invalid/secret.png)",
        "sources": [],
        "expert_blocks": [
            {
                "module_id": "expert:synthetic",
                "version": "a" * 64,
                "text": "专家已有文字",
                "artifact_stage": "draft",
                "review_state": "deferred",
                "knowledge_verified": False,
                "evidence_ids": ["ev-one"],
                "object_ids": ["ko-one"],
                "behavior": "abstain",
                "media": [
                    {
                        "resource_id": "vo-one",
                        "kind": "image",
                        "caption": "来源图 <script>",
                        "status": "unsupported",
                        "reason": "output_capability_unknown",
                    }
                ],
            }
        ],
    }


def available(module, version, resource):
    return {
        "module_id": module,
        "version": version,
        "resource_id": resource,
        "status": "available",
        "media_type": "image/png",
        "sha256": digest(PNG),
        "data_base64": base64.b64encode(PNG).decode(),
    }


@pytest.mark.parametrize("capability", ["unknown", "text", "images", "full"])
def test_ordinary_notes_never_read_images_or_gain_placeholders(capability):
    packet = {"answer": "![图](private.png) ![[vault-image.png]] <img src=x>", "sources": []}
    assert (
        output.project(packet, capability, lambda *args: pytest.fail("ordinary media read"))
        is packet
    )
    assert query.render(packet, "普通库", capability) == packet["answer"]
    with zipfile.ZipFile(io.BytesIO(output.markdown_archive(packet))) as archive:
        assert archive.namelist() == ["answer.md"]
        assert "!\\[图\\]" in archive.read("answer.md").decode()


@pytest.mark.parametrize("capability", ["unknown", "text"])
def test_text_capabilities_do_not_read_binary_and_preserve_abstention(packet, capability):
    result = output.project(packet, capability, lambda *args: pytest.fail("binary read"))
    assert result["presentation"]["state"] == (
        "unknown" if capability == "unknown" else "text_only"
    )
    assert "保持弃答" in output.plain(result)
    assert result["expert_blocks"][0]["text"] == "专家已有文字"
    assert "data_base64" not in json.dumps(result)


def test_only_explicit_ids_are_read_once_and_failures_are_local(packet):
    media = packet["expert_blocks"][0]["media"]
    media.extend([copy.deepcopy(media[0]), {**media[0], "resource_id": "vo-missing"}])
    calls = []

    def read(module, version, resource):
        calls.append(resource)
        if resource == "vo-missing":
            return {
                "module_id": module,
                "version": version,
                "resource_id": resource,
                "status": "missing",
                "reason": "resource_file_missing",
            }
        return available(module, version, resource)

    result = output.project(packet, "full", read)
    assert calls == ["vo-one", "vo-missing"]
    assert result["presentation"]["state"] == "partially_supported"
    assert result["answer"] == packet["answer"]
    assert result["expert_blocks"][0]["media"][2]["reason"] == "resource_file_missing"
    assert packet["expert_blocks"][0]["media"][0]["status"] == "unsupported"


@pytest.mark.parametrize("status", ["missing", "unauthorized", "unsupported", "stale"])
def test_resource_failure_preserves_text_and_exact_state(packet, status):
    def read(module, version, resource):
        return {"module_id": module, "version": version, "resource_id": resource, "status": status}

    result = output.project(packet, "full", read)
    assert result["expert_blocks"][0]["media"][0]["status"] == status
    assert "专家已有文字" in output.plain(result)


def test_mismatched_binding_and_unverified_grid_cannot_supply_media(packet):
    wrong = output.project(packet, "full", lambda *args: available("expert:other", *args[1:]))
    assert wrong["expert_blocks"][0]["media"][0]["status"] == "stale"
    packet["expert_blocks"][0]["media"][0].update(kind="table", rows=[["a"], ["2"]])
    result = output.project(packet, "full", available)
    assert result["expert_blocks"][0]["media"][0]["render_as"] == "image"


def test_verified_rows_are_required_for_real_table_and_markdown(packet):
    def read(*args):
        return {**available(*args), "rows_verified": True, "rows": [["列一", "列二"], ["a|b", 2]]}

    result = output.project(packet, "full", read)
    assert result["expert_blocks"][0]["media"][0]["render_as"] == "table"
    mcp = expert_mcp.result(result, "full")
    assert "| 列一 | 列二 |" in "\n".join(c.text for c in mcp.content if c.type == "text")
    assert all(c.type != "image" for c in mcp.content)
    with zipfile.ZipFile(io.BytesIO(output.markdown_archive(result))) as archive:
        text = archive.read("answer.md").decode()
        assert "| 列一 | 列二 |" in text and "a\\|b" in text
        assert archive.namelist() == ["answer.md"]

    def table_without_image(*args):
        value = read(*args)
        value.pop("data_base64")
        return value

    fallback = output.project(packet, "images", table_without_image)
    assert fallback["expert_blocks"][0]["media"][0]["reason"] == "output_table_unsupported"
    assert "已有文字保留" in output.plain(fallback)


def test_markdown_is_self_contained_and_mcp_images_do_not_claim_user_visibility(packet):
    result = output.project(packet, "full", available)
    with zipfile.ZipFile(io.BytesIO(output.markdown_archive(result))) as archive:
        path = f"assets/{digest(PNG)}.png"
        assert archive.read(path) == PNG
        text = archive.read("answer.md").decode()
        assert f"]({path})" in text and "保持弃答" in text
        assert "<script>" not in text
    mcp = expert_mcp.result(result, "full")
    assert [c.type for c in mcp.content] == ["text", "text", "image"]
    assert mcp.structuredContent["mcp_presentation"]["user_visible"] == "unconfirmed"
    assert "data_base64" not in json.dumps(mcp.structuredContent)
    assert "ev-one" in json.dumps(mcp.structuredContent)
    assert "![private](https://example.invalid/secret.png)" not in mcp.content[0].text
    assert "!\\[private\\]" in mcp.content[0].text
    note = {"module_id": "note:ordinary", "text": "普通笔记文字", "kind": "note"}
    assert expert_mcp.result(note, "full") is note


@pytest.fixture
def sdk(monkeypatch):
    class Builder:
        def __init__(self):
            self.value = {}

        def __getattr__(self, name):
            def assign(value):
                self.value[name] = value
                return self

            return assign

        def build(self):
            return SimpleNamespace(**self.value)

    model = SimpleNamespace(builder=Builder)
    monkeypatch.setitem(
        sys.modules,
        "lark_oapi.api.im.v1",
        SimpleNamespace(
            CreateImageRequest=model,
            CreateImageRequestBody=model,
            CreateMessageRequest=model,
            CreateMessageRequestBody=model,
        ),
    )
    return model


@pytest.mark.parametrize("upload", ["ok", "failed", "unknown"])
def test_rich_sdk_uses_single_post_upload_failure_is_local(packet, sdk, upload):
    packet["expert_blocks"][0]["media"].append(
        copy.deepcopy(packet["expert_blocks"][0]["media"][0])
    )
    projected = output.project(packet, "full", available)
    body = query.render(packet, "本库", "full", available)
    assert isinstance(body, dict) and body["response"] == projected
    calls, checked = [], []

    def image(request):
        calls.append(("image", request.request_body.image.read()))
        if upload == "unknown":
            raise TimeoutError()
        return SimpleNamespace(
            success=lambda: upload == "ok", data=SimpleNamespace(image_key="img_one")
        )

    def message(request):
        calls.append(("message", request.request_body))
        return SimpleNamespace(
            success=lambda: True, data=SimpleNamespace(message_id="om_synthetic")
        )

    client = SimpleNamespace(
        im=SimpleNamespace(
            v1=SimpleNamespace(
                image=SimpleNamespace(create=image), message=SimpleNamespace(create=message)
            )
        )
    )

    def guard():
        assert not checked
        checked.append(True)

    rechecks = []
    guard.recheck = lambda: rechecks.append(True)
    result = expert_feishu_render.send(client, "ou_owner", body, guard)
    assert result["category"] == "confirmed"
    assert [kind for kind, _ in calls] == ["image", "message"]
    sent = calls[-1][1]
    assert sent.msg_type == "post" and rechecks == [True, True]
    content = json.loads(sent.content)
    assert "普通文字" in json.dumps(content, ensure_ascii=False)
    assert "仅供参考" in json.dumps(content, ensure_ascii=False)
    assert all(
        value not in json.dumps(content, ensure_ascii=False)
        for value in (
            "ev-one",
            "ko-one",
            "vo-one",
            "expert:synthetic",
            "a" * 64,
            "证据 ID",
            "对象 ID",
        )
    )
    assert ("img_one" in json.dumps(content)) == (upload == "ok")
    if upload != "ok":
        assert "上传" in json.dumps(content, ensure_ascii=False)
    if upload == "unknown":
        assert json.dumps(content, ensure_ascii=False).count("上传结果未知") == 2


def test_reauthorization_failure_stops_before_any_upload(packet, sdk):
    body = query.render(packet, "本库", "full", available)

    def guard():
        pass

    def withdrawn():
        raise PermissionError("withdrawn")

    guard.recheck = withdrawn
    client = SimpleNamespace(
        im=SimpleNamespace(
            v1=SimpleNamespace(
                image=SimpleNamespace(create=lambda *a: pytest.fail("revoked upload"))
            )
        )
    )
    with pytest.raises(PermissionError):
        expert_feishu_render.send(client, "ou_owner", body, guard)
