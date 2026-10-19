"""Weixin's actual HTTP contract with a synthetic transport, including durable attempt receipts."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from test_expert_media_outputs import available
from test_expert_media_outputs import packet as packet
from test_message_channels import bind
from test_message_channels import channel as channel

from fiber_bundle.channels import dispatch, query, store, weixin, weixin_output
from fiber_bundle.config import InvalidInput
from fiber_bundle.inputs import canonical


class Response:
    status = 200
    headers = {"x-encrypted-param": "synthetic-encrypted-param"}

    def __init__(self, value):
        self.value = value
        self.content = self

    async def __aenter__(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self

    async def __aexit__(self, *args):
        pass

    async def read(self, size):
        return json.dumps(self.value).encode()


class Session:
    def __init__(self, *, upload_failure=False, image_result=None):
        self.requests = []
        self.upload_failure = upload_failure
        self.image_result = image_result

    def post(self, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        body = (
            json.loads(kwargs["data"])
            if isinstance(kwargs["data"], bytes) and "cdn.weixin" not in url
            else None
        )
        self.requests.append((url, body))
        if url.endswith("getuploadurl"):
            return Response(
                {"ret": 7}
                if self.upload_failure
                else {
                    "ret": 0,
                    "upload_full_url": "https://novac2c.cdn.weixin.qq.com/c2c/upload?opaque=test",
                }
            )
        if body and body.get("msg", {}).get("item_list", [{}])[0].get("type") == 2:
            return Response(self.image_result if self.image_result is not None else {"ret": 0})
        return Response({"ret": 0})


def runtime(session):
    return SimpleNamespace(
        _send_session=session,
        _send_text_gate=asyncio.Lock(),
        _base_url="https://ilinkai.weixin.qq.com",
        _cdn_base_url="https://novac2c.cdn.weixin.qq.com/c2c",
        _token="synthetic-token",
    ), SimpleNamespace(
        _base_info=lambda: {},
        _headers=lambda *args: {},
        MEDIA_IMAGE=1,
        ITEM_IMAGE=2,
        _aes128_ecb_encrypt=lambda raw, key: raw + b"padding",
    )


def guarded():
    calls, receipts = [], {}

    def guard():
        assert not calls
        calls.append(True)

    def part(key, kind, state, reason=""):
        if state == "sending":
            assert key not in receipts
        else:
            assert receipts[key]["state"] == "sending"
        receipts[key] = {"kind": kind, "state": state, "reason": reason}

    guard.recheck = lambda: None
    guard.part = part
    return guard, receipts


def messages(session):
    return [b["msg"]["item_list"][0] for _, b in session.requests if b and "msg" in b]


def run_output(body, session=None):
    session = session or Session()
    adapter, module = runtime(session)
    guard, receipts = guarded()
    result = asyncio.run(
        weixin_output.send(
            adapter, module, "owner", body, "synthetic-context", guard, lambda a: None
        )
    )
    return result, session, receipts


def test_ordinary_text_never_uploads_and_preserves_unicode_without_truncation():
    text = "普通笔记 ![[x.png]] 热管理" * 300
    success, session, receipts = run_output(text)
    assert success
    sent = messages(session)
    assert all(x["type"] == 1 for x in sent)
    assert "".join(x["text_item"]["text"] for x in sent) == text
    assert all(len(x["text_item"]["text"].encode()) <= 1800 for x in sent)
    assert all(x["state"] == "sent" for x in receipts.values())
    assert len(session.requests) == len(sent)


def test_expert_image_dedup_and_verified_table_are_sent_in_order(packet):
    body = query.render(packet, "库", "full", available, platform="weixin")
    block = body["response"]["expert_blocks"][0]
    block["media"].append(copy.deepcopy(block["media"][0]))
    block["media"].append(
        {
            **available("expert:synthetic", "a" * 64, "vo-table"),
            "resource_id": "vo-table",
            "render_as": "table",
            "rows_verified": True,
            "rows": [["材料", "热导率"], ["铜", 400]],
            "caption": "材料表",
        }
    )
    success, session, receipts = run_output(body)
    assert success
    sent = messages(session)
    assert [x["type"] for x in sent] == [1, 2, 1]
    assert "铜 | 400" in sent[-1]["text_item"]["text"]
    assert sum(x["kind"] == "upload" for x in receipts.values()) == 1
    assert sum(x["state"] == "uploaded" for x in receipts.values()) == 1
    assert sent[0]["text_item"]["text"].startswith(body["text"])
    assert all("data_base64" not in str(x) and "token" not in str(x) for x in receipts.values())


@pytest.mark.parametrize("failure", ["upload", "rejected", "unknown", "malformed"])
def test_failures_keep_answer_unknown_send_stops_and_no_send_is_retried(packet, failure):
    session = Session(
        upload_failure=failure == "upload",
        image_result={
            "rejected": {"ret": 5},
            "unknown": TimeoutError(),
            "malformed": {"ret": None},
        }.get(failure),
    )
    body = query.render(packet, "库", "images", available, platform="weixin")
    success, session, receipts = run_output(body, session)
    sent = messages(session)
    assert sent[0]["text_item"]["text"].startswith(body["text"])
    assert sum(x["type"] == 2 for x in sent) <= 1
    assert success is (failure in {"upload", "rejected"})
    if failure in {"unknown", "malformed"}:
        assert sent[-1]["type"] == 2
        assert any(x["state"] == "unknown" for x in receipts.values())
    else:
        assert "保留" in sent[-1]["text_item"]["text"] or "失败" in sent[-1]["text_item"]["text"]
        assert any(x["state"] == "failed" for x in receipts.values())


def test_missing_permission_is_text_fallback_without_upload(packet):
    body = query.render(
        packet,
        "库",
        "full",
        lambda m, v, r: {
            "module_id": m,
            "version": v,
            "resource_id": r,
            "status": "unauthorized",
            "reason": "resource_permission_denied",
        },
        platform="weixin",
    )
    success, session, _ = run_output(body)
    assert success and len(session.requests) == 2
    assert "无图表读取权限" in messages(session)[-1]["text_item"]["text"]


def test_pause_between_parts_stops_without_fallback_or_upload(packet):
    session = Session()
    adapter, module = runtime(session)
    guard, receipts = guarded()

    def recheck():
        if session.requests:
            raise InvalidInput("channel_scope_withdrawn")

    guard.recheck = recheck
    body = query.render(packet, "库", "images", available, platform="weixin")
    with pytest.raises(InvalidInput, match="withdrawn"):
        asyncio.run(
            weixin_output.send(adapter, module, "owner", body, "context", guard, lambda a: None)
        )
    assert len(session.requests) == 1


@pytest.mark.parametrize(
    "target",
    [
        "http://novac2c.cdn.weixin.qq.com/x",
        "https://evil.invalid/x",
        "https://user@novac2c.cdn.weixin.qq.com/x",
    ],
)
def test_upload_cannot_follow_external_or_credential_routes(target):
    with pytest.raises(InvalidInput, match="route_invalid"):
        weixin_output.cdn_route(target)


def test_persistent_receipts_and_duplicate_event_are_not_replayed(config, channel, monkeypatch):
    value, _ = channel
    bind(config, value)
    with store.opened(config) as db:
        current = store.get(db, value["id"])
        current["value"]["platform"] = "weixin"
        db.execute(
            "UPDATE channels SET value=? WHERE id=?", (canonical(current["value"]), value["id"])
        )
    assert dispatch.capture(
        config, value["id"], 1, "wx-one", "ou_synthetic", "查询本库：压力", "context"
    )
    dispatch.advance(config, value["id"], 1)
    session = Session()
    adapter, module = runtime(session)
    monkeypatch.setattr(weixin, "upstream", lambda: module)
    monkeypatch.setattr(weixin, "preflight", lambda a: None)

    async def sender(owner, body, context, guard):
        return await weixin.send(adapter, owner, body, context, guard)

    assert asyncio.run(dispatch.deliver(config, value["id"], 1, sender)) == "sent"
    assert asyncio.run(dispatch.deliver(config, value["id"], 1, sender)) == "idle"
    assert not dispatch.capture(
        config, value["id"], 1, "wx-one", "ou_synthetic", "查询本库：压力", "context"
    )
    with store.readonly(config) as db:
        data = json.loads(
            db.execute("select value from events where event_id=?", ("wx-one",)).fetchone()[0]
        )
    assert data["delivery_parts"]["1"]["state"] == "sent"
    assert len(session.requests) == 1


def test_crash_receipt_survives_restart_and_blocks_replay(config, channel):
    value, _ = channel
    bind(config, value)
    with store.opened(config) as db:
        current = store.get(db, value["id"])
        current["value"]["platform"] = "weixin"
        db.execute(
            "UPDATE channels SET value=? WHERE id=?", (canonical(current["value"]), value["id"])
        )
    dispatch.capture(
        config, value["id"], 1, "wx-crash", "ou_synthetic", "查询本库：压力", "context"
    )
    dispatch.advance(config, value["id"], 1)

    async def crash(owner, body, context, guard):
        guard()
        guard.part("1", "text", "sending")
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dispatch.deliver(config, value["id"], 1, crash))
    with store.readonly(config) as db:
        row = db.execute(
            "select state,value from events where event_id=?", ("wx-crash",)
        ).fetchone()
        assert row["state"] == "sending"
        assert json.loads(row["value"])["delivery_parts"]["1"]["state"] == "sending"
    with pytest.raises(InvalidInput, match="reconciliation"):
        asyncio.run(dispatch.deliver(config, value["id"], 1, crash))


def test_receipt_claim_cannot_be_reused_even_after_confirmed_success(config, channel):
    from fiber_bundle.channels.delivery_parts import attach, public

    value, _ = channel
    bind(config, value)
    with store.opened(config) as db:
        db.execute("INSERT INTO events VALUES(?,?,1,'sending','{}',0)", (value["id"], "receipt"))

    def guard():
        pass

    attach(config, value["id"], "receipt", guard)
    guard.part("1", "image", "sending")
    guard.part("1", "image", "sent")
    with pytest.raises(InvalidInput, match="replay_forbidden"):
        guard.part("1", "image", "sending")
    with store.readonly(config) as db:
        raw = db.execute("select value from events where event_id='receipt'").fetchone()[0]
    assert public(raw) == [{"part": "1", "kind": "image", "state": "sent", "reason": ""}]


@pytest.mark.parametrize("status", [{}, {"errcode": 0}, {"ret": 0}])
def test_omitted_zero_status_accepts_text_image_and_upload_without_duplicate_sends(packet, status):
    class OmittedZeroSession(Session):
        def post(self, url, **kwargs):
            response = super().post(url, **kwargs)
            if "cdn.weixin" not in url:
                response.value.pop("ret", None)
                response.value.update(status)
            return response

    body = query.render(packet, "库", "images", available, platform="weixin")
    success, session, receipts = run_output(body, OmittedZeroSession())
    assert success
    assert [item["type"] for item in messages(session)] == [1, 2]
    assert all(item["state"] in {"sent", "uploaded"} for item in receipts.values())
    assert sum(item["kind"] == "image" for item in receipts.values()) == 1


@pytest.mark.parametrize(
    "status",
    [
        {"ret": None},
        {"ret": False},
        {"ret": "0"},
        {"errcode": None},
        {"errcode": False},
        {"errcode": "0"},
    ],
)
def test_malformed_status_is_unknown_and_never_retried(status):
    class MalformedSession(Session):
        def post(self, url, **kwargs):
            response = super().post(url, **kwargs)
            response.value = status
            return response

    success, session, receipts = run_output("合成文字", MalformedSession())
    assert not success
    assert len(messages(session)) == 1
    assert receipts["1"]["state"] == "unknown"


def test_five_distinct_images_use_one_caption_list_and_six_message_sends(packet):
    import base64
    import struct
    import zlib

    from fiber_bundle.objects import digest

    body = query.render(packet, "库", "images", available, platform="weixin")
    media = []
    for index in range(5):
        item = available("expert:synthetic", "a" * 64, f"vo-{index}")
        png = base64.b64decode(item["data_base64"])
        data = b"fixture\0" + str(index).encode()
        chunk = struct.pack(">I", len(data)) + b"tEXt" + data
        chunk += struct.pack(">I", zlib.crc32(b"tEXt" + data))
        raw = png[:-12] + chunk + png[-12:]
        media.append(
            {
                **item,
                "sha256": digest(raw),
                "data_base64": base64.b64encode(raw).decode(),
                "caption": f"合成图{index + 1}",
            }
        )
    body["response"]["expert_blocks"][0]["media"] = media
    success, session, receipts = run_output(body)
    sent = messages(session)
    assert success and [item["type"] for item in sent] == [1, 2, 2, 2, 2, 2]
    intro = sent[0]["text_item"]["text"]
    assert intro.startswith(body["text"])
    for index in range(5):
        assert intro.count(f"{index + 1}. 合成图{index + 1}") == 1
    assert sum(item["kind"] == "upload" for item in receipts.values()) == 5
    assert all(item["state"] in {"sent", "uploaded"} for item in receipts.values())


def test_rejection_records_only_bounded_numeric_codes_without_server_payload(packet):
    body = query.render(packet, "库", "images", available, platform="weixin")
    session = Session(image_result={"ret": -2, "errmsg": "private-token-must-not-leak"})
    success, _, receipts = run_output(body, session)
    assert success  # The known rejected image receives a successful text fallback.
    failed = [item for item in receipts.values() if item["state"] == "failed"]
    assert failed == [{"kind": "image", "state": "failed", "reason": "platform_rejected:ret=-2"}]
    assert "private-token" not in json.dumps(receipts)


def test_invalid_expert_text_is_rejected_before_any_network_call():
    session = Session()
    with pytest.raises(InvalidInput, match="channel_message_format_invalid"):
        run_output(
            {"schema": "fiber-expert-channel-output/v1", "text": [], "response": {}}, session
        )
    assert not session.requests
