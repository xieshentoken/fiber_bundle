"""Isolated sender/receipt contracts with real journal state and bounded transport."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_weixin_confirmations import command
from test_weixin_confirmations import prompt as prompt
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows
from test_weixin_receiver import Adapter, revoke

from fiber_bundle import auth
from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_hermes_sender as sender
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_prompt_delivery as prompts
from fiber_bundle import weixin_receipt_delivery as receipts
from fiber_bundle import weixin_receiver as receiver
from fiber_bundle.config import IntegrityError, InvalidInput


class Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status
        self.content = self
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def iter_chunked(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]


def adapter_for(config, binding, response, calls):
    adapter = Adapter()
    receiver.configure(adapter, config, binding)
    adapter._running = True
    adapter._send_text_gate = asyncio.Lock()

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response

    adapter._send_session = SimpleNamespace(post=post)
    return adapter


async def send(adapter, owner, text):
    return await sender.send_once(
        adapter,
        owner,
        text,
        headers=lambda token, body: {"Authorization": "Bearer " + token},
        base_info=lambda: {"channel_version": "synthetic"},
    )


@pytest.mark.parametrize(
    "reply,status,success",
    [
        ({"ret": 0}, 200, True),
        ({"ret": 0, "errcode": 0}, 200, True),
        ({"ret": 0, "errcode": -14}, 200, False),
        ({"ret": -2}, 200, False),
        ({}, 200, False),
        ({"ret": False}, 200, False),
        ({"ret": "0"}, 200, False),
        ({"ret": 0, "errcode": False}, 200, False),
        ([], 200, False),
        ({"ret": 0}, 302, False),
        ({"ret": 0}, 500, False),
        (b"not json", 200, False),
        (b"x" * 65537, 200, False),
    ],
)
def test_one_post_strict_ack_no_retry_or_redirect(config, binding, prompt, reply, status, success):
    raw = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
    response = Response(raw, status)
    calls = []
    adapter = adapter_for(config, binding, response, calls)
    result = asyncio.run(
        prompts.deliver(
            config, binding, prompt[1]["prompt_id"], lambda owner, text: send(adapter, owner, text)
        )
    )
    assert result["state"] == ("sent" if success else "unknown")
    asyncio.run(
        prompts.deliver(
            config, binding, prompt[1]["prompt_id"], lambda owner, text: send(adapter, owner, text)
        )
    )
    assert len(calls) == 1 and response.closed
    url, kwargs = calls[0]
    assert url.endswith("/ilink/bot/sendmessage") and kwargs["allow_redirects"] is False
    payload = json.loads(kwargs["data"])
    assert payload["msg"]["to_user_id"] == binding.owner_id
    assert payload["msg"]["context_token"] == "synthetic-private-route"
    assert payload["msg"]["item_list"] == [{"type": 1, "text_item": {"text": prompt[1]["text"]}}]
    assert "synthetic-private-route" not in json.dumps(result)


@pytest.mark.parametrize("mode", ["owner", "account", "revoked", "context", "digest", "stopped"])
def test_send_guards_before_post(config, binding, prompt, mode):
    calls = []
    adapter = adapter_for(config, binding, Response(b'{"ret":0}'), calls)
    owner = binding.owner_id
    if mode == "owner":
        owner = "other"
    elif mode == "account":
        adapter._account_id = "other"
    elif mode == "revoked":
        revoke(config, binding)
    elif mode == "context":
        inbox.receive(
            config, binding, inbox.cursor(config, binding), poll(message("new", context_token=""))
        )
    elif mode == "digest":
        with inbox.connection(config, binding) as db:
            db.execute("UPDATE events SET raw_sha256='corrupt'")
    else:
        adapter._running = False
    with pytest.raises((InvalidInput, IntegrityError, auth.Denied)):
        asyncio.run(send(adapter, owner, "确认"))
    assert calls == []


def test_latest_context_and_recheck_after_gate_wait(config, binding, prompt):
    calls = []
    adapter = adapter_for(config, binding, Response(b'{"ret":0}'), calls)
    inbox.receive(
        config, binding, inbox.cursor(config, binding), poll(message("new", context_token="fresh"))
    )
    assert asyncio.run(send(adapter, binding.owner_id, "确认")) is True
    assert json.loads(calls[0][1]["data"])["msg"]["context_token"] == "fresh"
    calls.clear()

    async def scenario():
        async with adapter._send_text_gate:
            task = asyncio.create_task(send(adapter, binding.owner_id, "确认"))
            await asyncio.sleep(0)
            revoke(config, binding)
        with pytest.raises(auth.Denied):
            await task

    asyncio.run(scenario())
    assert calls == []


@pytest.mark.parametrize("outcome", [True, False, "exception"])
def test_receipt_delivery_is_durable_and_exactly_once_attempted(config, binding, prompt, outcome):
    job = command(config, binding, f"批次 {prompt[1]['prompt_id']} 仅用于咨询")
    confirmations.process(config, binding)
    prepared = receipts.prepare(config, binding, job)
    assert "请补充咨询问题" in prepared["text"] and "该命令处理时" in prepared["text"]
    calls = []

    async def callback(owner, text):
        calls.append((owner, text))
        if outcome == "exception":
            raise OSError("private third party details")
        return outcome

    result = asyncio.run(receipts.deliver(config, binding, job, callback))
    assert result["state"] == ("sent" if outcome is True else "unknown")
    assert asyncio.run(receipts.deliver(config, binding, job, callback)) == result
    assert calls == [(binding.owner_id, prepared["text"])]
    assert "private" not in json.dumps(result)
    assert rows(config, "wx_batches")[0]["state"] == "selected"


def test_rejected_command_receipt_and_sources_not_sendable(config, binding, prompt):
    job = command(config, binding, "批次 unknown 错误操作")
    confirmations.process(config, binding)
    assert "未接受" in receipts.prepare(config, binding, job)["text"]
    with pytest.raises(InvalidInput, match="unavailable"):
        receipts.prepare(config, binding, rows(config, "events")[0]["job_id"])


def test_receipt_change_detected(config, binding, prompt):
    job = command(config, binding, f"批次 {prompt[1]['prompt_id']} 取消")
    confirmations.process(config, binding)
    receipts.prepare(config, binding, job)
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE wx_command_receipts SET result_json='{}' WHERE kind='command'")
    with pytest.raises(IntegrityError, match="changed"):
        receipts.prepare(config, binding, job)


def test_receipt_cancelled_or_concurrent_send_never_repeated(config, binding, prompt):
    job = command(config, binding, f"批次 {prompt[1]['prompt_id']} 取消")
    confirmations.process(config, binding)
    calls = []

    async def scenario():
        entered = asyncio.Event()

        async def callback(*args):
            calls.append(args)
            entered.set()
            await asyncio.Future()

        task = asyncio.create_task(receipts.deliver(config, binding, job, callback))
        await entered.wait()
        assert (await receipts.deliver(config, binding, job, callback))["state"] == "sending"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await receipts.deliver(config, binding, job, callback))["state"] == "sending"

    asyncio.run(scenario())
    assert len(calls) == 1


def test_real_tls_prompt_and_receipt_transport(tmp_path, config, binding, prompt):
    aiohttp = pytest.importorskip("aiohttp")
    import ssl
    from datetime import UTC, datetime, timedelta

    from aiohttp import web
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert = tmp_path / "cert.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private = tmp_path / "key.pem"
    private.touch(mode=0o600)
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(cert, private)
    client_ssl = ssl.create_default_context(cafile=str(cert))
    requests = []

    async def scenario():
        async def handler(request):
            requests.append(await request.json())
            assert request.headers["Authorization"] == "Bearer synthetic-secret"
            assert request.content_length == len(
                json.dumps(requests[-1], ensure_ascii=False, separators=(",", ":")).encode()
            )
            if len(requests) == 1:
                return web.json_response({"ret": 0})
            raise web.HTTPTemporaryRedirect("/should-not-follow")

        async def forbidden(request):
            pytest.fail("outbound redirect followed")

        app = web.Application()
        app.router.add_post("/ilink/bot/sendmessage", handler)
        app.router.add_post("/should-not-follow", forbidden)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ssl)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=client_ssl)
            ) as session:
                adapter = adapter_for(config, binding, None, [])
                adapter._base_url = f"https://localhost:{port}"
                adapter._send_session = session

                async def callback(owner, text):
                    return await send(adapter, owner, text)

                assert (await prompts.deliver(config, binding, prompt[1]["prompt_id"], callback))[
                    "state"
                ] == "sent"
                job = command(config, binding, f"批次 {prompt[1]['prompt_id']} 取消")
                confirmations.process(config, binding)
                assert (await receipts.deliver(config, binding, job, callback))[
                    "state"
                ] == "unknown"
                assert (await receipts.deliver(config, binding, job, callback))[
                    "state"
                ] == "unknown"
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
    assert len(requests) == 2
    assert all(r["msg"]["to_user_id"] == binding.owner_id for r in requests)
    assert "批次" in requests[0]["msg"]["item_list"][0]["text_item"]["text"]
    assert "操作回执" in requests[1]["msg"]["item_list"][0]["text_item"]["text"]


def test_revocation_after_receipt_post_leaves_unresolved_attempt(config, binding, prompt):
    job = command(config, binding, f"批次 {prompt[1]['prompt_id']} 取消")
    confirmations.process(config, binding)

    async def callback(*args):
        revoke(config, binding)
        return True

    with pytest.raises(auth.Denied):
        asyncio.run(receipts.deliver(config, binding, job, callback))
    assert rows(config, "wx_receipt_deliveries")[0]["state"] == "sending"


def test_receipt_revoked_before_delivery_never_calls_sender(config, binding, prompt):
    job = command(config, binding, f"批次 {prompt[1]['prompt_id']} 取消")
    confirmations.process(config, binding)
    revoke(config, binding)

    async def forbidden(*args):
        pytest.fail("revoked receipt must not send")

    with pytest.raises(auth.Denied):
        asyncio.run(receipts.deliver(config, binding, job, forbidden))
