"""Shared batch/Store behavior with Feishu originals and isolated synthetic authorization."""

import asyncio
import copy
import io
import json
import zipfile
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_weixin_inbox import binding as binding

from fiber_bundle import admin_batches, auth, database, feishu_archives, feishu_events
from fiber_bundle import feishu_receiver as receiver
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_recovery as recovery
from fiber_bundle import weixin_store as handoff
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.store import Store
from fiber_bundle.weixin_events import MAX_BYTES


@pytest.fixture
def feishu(config, binding):
    value = replace(binding, account_id="feishu:cli_synthetic", owner_id="ou_owner")
    receiver.initialize(config, value)
    return value


def payload(kind="text", body=None, identity="message-1"):
    return {
        "header": {"app_id": "cli_synthetic", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_owner"}},
            "message": {
                "message_id": identity,
                "chat_id": "oc_private",
                "chat_type": "p2p",
                "message_type": kind,
                "content": json.dumps(
                    {"text": "https://mp.weixin.qq.com/s/synthetic"} if body is None else body
                ),
            },
        },
    }


def archive(entries=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        for name, data in entries or [
            ("chat.txt", "青松\n测试压力：2.4 MPa".encode()),
            ("image.jpg", b"\xff\xd8\xffsynthetic"),
        ]:
            zipped.writestr(name, data)
    return stream.getvalue()


def row(config, feishu):
    with inbox.connection(config, feishu) as db:
        return dict(
            db.execute("SELECT * FROM events WHERE account_id=?", (feishu.account_id,)).fetchone()
        )


def freeze(config, feishu):
    receiver.advance(config, feishu)
    from datetime import UTC, datetime, timedelta

    views = batches.advance(config, feishu, now=datetime.now(UTC) + timedelta(seconds=5))
    return views[0]["batch_id"]


def test_link_original_freeze_choose_and_idempotent_store(config, feishu):
    original = payload()
    receiver.capture(config, feishu, original)
    receiver.capture(config, feishu, original)
    receiver.initialize(config, feishu)  # Restart does not lose the fixed source.
    batch = freeze(config, feishu)
    assert admin_batches.pending(config)[0]["count"] == 1
    with pytest.raises(InvalidInput):
        handoff.transfer(config, feishu, batch)
    batches.choose(config, feishu, batch, "save_one")
    result = handoff.transfer(config, feishu, batch)
    assert handoff.transfer(config, feishu, batch) == result
    assert not result["published"] and not result["dispatched"]
    main = database.connect(config.state_root)
    try:
        receipt = main.execute("SELECT * FROM receipts").fetchone()
        assert receipt["ingress_scope"].startswith("feishu:")
        raw = json.loads(Store(config).read_asset(receipt["raw_asset_id"], vault="personal"))
        source = raw["sources"][0]
        assert source["platform"] == "feishu" and source["raw_event"] == original
        assert source["platform_completeness"] == "not_verified"
        for table in ("model_calls", "execution_runs", "document_tasks", "article_publications"):
            assert main.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        main.close()


@pytest.mark.parametrize(
    "field,value", [("sender_type", "app"), ("open_id", "ou_other"), ("chat_type", "group")]
)
def test_untrusted_sender_and_groups_never_enter_journal(config, feishu, field, value):
    event = payload()
    if field == "sender_type":
        event["event"]["sender"][field] = value
    elif field == "open_id":
        event["event"]["sender"]["sender_id"][field] = value
    else:
        event["event"]["message"][field] = value
    assert receiver.capture(config, feishu, event) == {"state": "ignored"}
    with inbox.connection(config, feishu) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_identity_conflict_and_cross_app_rejected(config, feishu):
    event = payload()
    receiver.capture(config, feishu, event)
    changed = copy.deepcopy(event)
    changed["event"]["message"]["content"] = '{"text":"changed"}'
    with pytest.raises(IntegrityError):
        receiver.capture(config, feishu, changed)
    changed["header"]["app_id"] = "cli_other"
    with pytest.raises(InvalidInput):
        receiver.capture(config, feishu, changed)
    assert json.loads(row(config, feishu)["raw"])["fiber_feishu"]["raw_event"] == event


def test_zip_recovery_preserves_bytes_and_unknown_original_metadata(config, feishu):
    event = payload("file", {"file_key": "file_synthetic", "file_name": "微信记录.zip"})
    receiver.capture(config, feishu, event)
    captured = row(config, feishu)
    assert captured["state"] == "incomplete"
    batch = freeze(config, feishu)
    with pytest.raises(InvalidInput):
        batches.choose(config, feishu, batch, "save_one")
    data = archive()
    recovery.complete(config, feishu, captured["job_id"], captured["raw_sha256"], {"0": data})
    batches.choose(config, feishu, batch, "save_one")
    result = handoff.transfer(config, feishu, batch)
    assert len(result["receipts"]) == 1
    main = database.connect(config.state_root)
    try:
        entries = main.execute("SELECT * FROM assets").fetchall()
        stored = [Store(config).read_asset(r["id"], vault="personal") for r in entries]
        assert data in stored and "青松\n测试压力：2.4 MPa".encode() in stored
        types = main.execute("SELECT media_type FROM version_assets").fetchall()
        assert {r["media_type"] for r in types} >= {"application/zip", "text/plain", "image/jpeg"}
    finally:
        main.close()
    metadata = json.loads(captured["raw"])["fiber_feishu"]
    assert metadata["original_chat_message_count"] is None
    assert metadata["original_chat_sender"] is None
    assert metadata["original_chat_sent_at"] is None


@pytest.mark.parametrize(
    "kind,body", [("interactive", {"card": "unknown"}), ("share_chat", {"chat_id": "private"})]
)
def test_unknown_types_retained_and_blocked(config, feishu, kind, body):
    receiver.capture(config, feishu, payload(kind, body))
    captured = row(config, feishu)
    assert captured["state"] == "unsupported"
    batch = freeze(config, feishu)
    with pytest.raises(InvalidInput):
        batches.choose(config, feishu, batch, "save_one")


def test_post_links_and_source_commands_are_not_executed(config, feishu):
    event = payload(
        "post",
        {
            "zh_cn": {
                "title": "文章",
                "content": [[{"tag": "a", "href": "https://mp.weixin.qq.com/s/synthetic"}]],
            }
        },
    )
    normalized = feishu_events.normalize(feishu, event)
    assert normalized["item_list"][1]["text_item"]["text"].startswith("https://mp.weixin")
    receiver.capture(config, feishu, payload(body={"text": "批次 abcdef0123456789 取消"}))
    batch = freeze(config, feishu)
    assert batches.get(config, feishu, batch)["state"] == "frozen"


def test_card_preserves_link_without_claiming_article_body(feishu):
    event = payload(
        "interactive", {"elements": [{"tag": "a", "href": "https://mp.weixin.qq.com/s/synthetic"}]}
    )
    event["header"]["token"] = "synthetic-transport-token"
    normalized = feishu_events.normalize(feishu, event)
    assert normalized["item_list"] == [
        {"type": 1, "text_item": {"text": "https://mp.weixin.qq.com/s/synthetic"}}
    ]
    assert "token" not in normalized["fiber_feishu"]["raw_event"]["header"]
    assert normalized["fiber_feishu"]["platform_completeness"] == "not_verified"


def test_revoke_blocks_capture_and_store(config, feishu):
    receiver.capture(config, feishu, payload())
    batch = freeze(config, feishu)
    batches.choose(config, feishu, batch, "save_one")
    main = database.connect(config.state_root)
    auth.set_grant(main, feishu.client_id, feishu.vault_id, query=False, original=False, add=False)
    main.close()
    with pytest.raises(auth.Denied):
        handoff.transfer(config, feishu, batch)
    with pytest.raises(auth.Denied):
        receiver.capture(config, feishu, payload(identity="new"))


@pytest.mark.parametrize(
    "name", ["../escape.txt", "/absolute.txt", "a\\escape.txt", "drive:file.txt"]
)
def test_unsafe_zip_never_extracts(name):
    with pytest.raises(InvalidInput):
        feishu_archives.assets(archive([(name, b"synthetic")]), "sample.zip")


def test_zip_utf8_and_expansion_bounds():
    for data in (archive([("sample.txt", b"\xff")]), b"not a zip"):
        with pytest.raises(InvalidInput):
            feishu_archives.assets(data, "sample.zip")
    with pytest.raises(InvalidInput):
        feishu_archives.assets(b"x" * (MAX_BYTES + 1), "sample.bin")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("expansion.txt", "a" * 50000)
    with pytest.raises(InvalidInput):
        feishu_archives.assets(stream.getvalue(), "sample.zip")


def test_download_durable_attempt_and_no_automatic_retry(config, feishu):
    receiver.capture(
        config, feishu, payload("file", {"file_key": "key", "file_name": "sample.zip"})
    )
    called = []

    class Adapter:
        _client = SimpleNamespace(
            im=SimpleNamespace(v1=SimpleNamespace(message_resource=SimpleNamespace(get=None)))
        )

        def _build_message_resource_request(self, **kwargs):
            called.append(kwargs)
            with inbox.connection(config, feishu) as db:
                assert (
                    db.execute("SELECT state FROM fiber_feishu_downloads").fetchone()[0]
                    == "running"
                )
            return kwargs

        async def _run_blocking(self, *_args):
            raise OSError("synthetic failure")

    with pytest.raises(OSError):
        asyncio.run(receiver.download_one(config, feishu, Adapter()))
    assert asyncio.run(receiver.download_one(config, feishu, Adapter())) is False
    assert len(called) == 1
    assert recovery.view(config, feishu, row(config, feishu)["job_id"])["state"] == "incomplete"
