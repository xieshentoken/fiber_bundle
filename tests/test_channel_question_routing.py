"""Production Feishu callback routing with synthetic SDK envelopes and real query ledger."""

import json
import sys
from types import SimpleNamespace

import pytest
from test_channel_natural_query import model_channel as model_channel
from test_message_channels import channel as channel
from test_model_configuration import vault_keychain as vault_keychain

from fiber_bundle.channels import feishu, store


@pytest.fixture
def receiver(config, model_channel, monkeypatch):
    value, _ = model_channel
    with store.readonly(config) as side:
        current = store.get(side, value["id"])
    # Enable the archival branch at the adapter boundary. Its capture spy below
    # records routing only; this query fixture never writes materials to a Vault.
    current["value"]["add"] = True
    settings = SimpleNamespace(app_id="synthetic-app", owner_id="ou_synthetic", binding=object())

    class Base:
        def __init__(self, configuration):
            pass

    monkeypatch.setitem(
        sys.modules,
        "plugins.platforms.feishu.adapter",
        SimpleNamespace(
            FeishuAdapter=Base, lark=SimpleNamespace(JSON=SimpleNamespace(marshal=json.dumps))
        ),
    )
    monkeypatch.setitem(
        sys.modules, "gateway.config", SimpleNamespace(PlatformConfig=SimpleNamespace)
    )
    monkeypatch.setattr(feishu.configuration, "feishu_settings", lambda *args: settings)
    monkeypatch.setattr(feishu.feishu_gateway_cli, "load_secret", lambda *args: "synthetic")
    monkeypatch.setattr(feishu.feishu_gateway, "adapter_class", lambda *args: Base)
    intake = []
    monkeypatch.setattr(
        feishu.feishu_receiver, "capture", lambda *args, **kwargs: intake.append(args[2])
    )
    return value, feishu.adapter(config, current), intake


def message(body, kind="text", *, owner="ou_synthetic", chat="p2p"):
    return {
        "header": {"app_id": "synthetic-app"},
        "event": {
            "sender": {"sender_type": "user", "sender_id": {"open_id": owner}},
            "message": {
                "message_id": "synthetic-request",
                "chat_type": chat,
                "message_type": kind,
                "content": json.dumps({"text": body}),
            },
        },
    }


@pytest.mark.parametrize("verb", ["列出", "列举", "给出", "罗列"])
def test_imperative_enters_query_once_and_never_archival_intake(config, receiver, verb):
    value, adapter, intake = receiver
    body = verb + "常见材料的热导率"
    payload = message(body)
    adapter._on_message_event(payload)
    adapter._on_message_event(payload)
    assert not intake
    with store.readonly(config) as side:
        rows = side.execute(
            "SELECT state,value FROM events WHERE channel_id=?", (value["id"],)
        ).fetchall()
    assert len(rows) == 1 and rows[0]["state"] == "captured"
    assert json.loads(rows[0]["value"])["question"] == body


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("保存：列出常见材料的热导率", "text"),
        ("入库：给出常见材料的热导率", "text"),
        ("归档：列举常见材料的热导率", "text"),
        ("提取常见材料的热导率", "text"),
        ("列出 https://example.com/article 中的热导率", "text"),
        ("列出常见材料的热导率", "file"),
        ("给出常见材料的热导率", "image"),
        ("转发聊天记录：列出常见材料的热导率", "text"),
    ],
)
def test_material_commands_links_and_attachments_keep_archival_route(config, receiver, body, kind):
    value, adapter, intake = receiver
    payload = message(body, kind)
    adapter._on_message_event(payload)
    assert intake == [payload]
    with store.readonly(config) as side:
        assert (
            side.execute(
                "SELECT count(*) FROM events WHERE channel_id=?", (value["id"],)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(("owner", "chat"), [("ou_other", "p2p"), ("ou_synthetic", "group")])
def test_new_requests_keep_owner_private_chat_boundary(config, receiver, owner, chat):
    value, adapter, intake = receiver
    adapter._on_message_event(message("列出常见材料的热导率", owner=owner, chat=chat))
    assert not intake
    with store.readonly(config) as side:
        assert (
            side.execute(
                "SELECT count(*) FROM events WHERE channel_id=?", (value["id"],)
            ).fetchone()[0]
            == 0
        )
