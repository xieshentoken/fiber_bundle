"""Candidate file protocol against an in-memory HTTPS session; no real transport."""

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from fiber_bundle import weixin_document_sender as sender
from fiber_bundle.config import InvalidInput
from fiber_bundle.inputs import AssetInput


class Content:
    def __init__(self, raw):
        self.raw = raw

    async def iter_chunked(self, size):
        for pos in range(0, len(self.raw), size):
            yield self.raw[pos : pos + size]


class Response:
    def __init__(self, value, *, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.content = Content(value if isinstance(value, bytes) else json.dumps(value).encode())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class Session:
    def __init__(self, allocation=None, ack=None, upload_headers=None):
        self.responses = [
            Response(allocation if allocation is not None else {"ret": 0, "upload_param": "a&b"}),
            Response(
                b"",
                headers=upload_headers
                if upload_headers is not None
                else {"x-encrypted-param": "encrypted-param"},
            ),
            Response(ack if ack is not None else {"ret": 0}),
        ]
        self.calls = []

    def post(self, url, **options):
        self.calls.append((url, options))
        return self.responses[len(self.calls) - 1]


@pytest.fixture
def adapter(monkeypatch):
    obj = SimpleNamespace(
        _send_text_gate=asyncio.Lock(),
        _fiber_capture_version=1,
        _running=True,
        _send_session=Session(),
        _token="synthetic-test-token",
        _base_url="https://api.example.invalid",
        _cdn_base_url="https://cdn.example.invalid",
    )
    monkeypatch.setattr(
        sender.receiver, "preflight", lambda _: (None, SimpleNamespace(owner_id="owner"))
    )
    monkeypatch.setattr(sender, "context", lambda *args: "synthetic-context")
    return obj


def send(adapter, guards):
    return asyncio.run(
        sender.send_once(
            adapter,
            "owner",
            AssetInput(b"unverified draft content", "draft.md", "text/markdown"),
            guards.append,
            headers=lambda token, body: {"Authorization": token},
            base_info=lambda: {"test": True},
        )
    )


def test_file_protocol_encrypts_exact_bytes_and_posts_each_stage_once(adapter):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    guards = []
    assert send(adapter, guards)
    assert guards == ["allocate", "upload", "send"]
    calls = adapter._send_session.calls
    assert len(calls) == 3 and all(v[1]["allow_redirects"] is False for v in calls)
    allocation = json.loads(calls[0][1]["data"])
    assert allocation["rawsize"] == 24 and allocation["media_type"] == 3
    assert calls[1][1]["headers"] == {"Content-Type": "application/octet-stream"}
    assert "upload_param=a%26b" in calls[1][0]
    key = bytes.fromhex(allocation["aeskey"])
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    plain = decryptor.update(calls[1][1]["data"]) + decryptor.finalize()
    assert plain[: -plain[-1]] == b"unverified draft content"
    msg = json.loads(calls[2][1]["data"])["msg"]
    item = msg["item_list"][0]
    assert msg["to_user_id"] == "owner" and msg["context_token"] == "synthetic-context"
    assert item["type"] == 4 and item["file_item"]["len"] == "24"
    assert bytes.fromhex(base64.b64decode(item["file_item"]["media"]["aes_key"]).decode()) == key


@pytest.mark.parametrize(
    "allocation,ack,upload_headers,count",
    [
        ({"ret": False}, None, None, 1),
        ({"ret": 0, "upload_full_url": "https://other.example.invalid/upload"}, None, None, 1),
        ({"ret": 0, "upload_full_url": "http://cdn.example.invalid/upload"}, None, None, 1),
        (None, None, {}, 2),
        (None, {"ret": False}, None, 3),
        (None, {"ret": 1}, None, 3),
        (None, {"ret": 0, "errcode": False}, None, 3),
    ],
)
def test_file_protocol_rejects_ambiguous_ack_or_route_without_retry(
    adapter, allocation, ack, upload_headers, count
):
    adapter._send_session = Session(allocation, ack, upload_headers)
    with pytest.raises(InvalidInput):
        send(adapter, [])
    assert len(adapter._send_session.calls) == count


def test_guard_denial_stops_before_upload_and_no_fallback(adapter):
    seen = []

    def guard(stage):
        seen.append(stage)
        if stage == "upload":
            raise InvalidInput("revoked")

    with pytest.raises(InvalidInput, match="revoked"):
        asyncio.run(
            sender.send_once(
                adapter,
                "owner",
                AssetInput(b"draft", "draft.txt", "text/plain"),
                guard,
                headers=lambda *args: {},
                base_info=lambda: {},
            )
        )
    assert seen == ["allocate", "upload"] and len(adapter._send_session.calls) == 1


def test_response_limit_and_invalid_base_route_block(adapter):
    adapter._base_url = "https://api.example.invalid?secret=no"
    with pytest.raises(InvalidInput, match="route_invalid"):
        send(adapter, [])
    assert not adapter._send_session.calls
    adapter._base_url = "https://api.example.invalid"
    adapter._send_session.responses[0] = Response(b"x" * 65537)
    with pytest.raises(InvalidInput, match="response_limit"):
        send(adapter, [])
    assert len(adapter._send_session.calls) == 1
