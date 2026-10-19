"""Original-descriptor recovery and real HTTP/TLS streaming in a synthetic server."""

import asyncio
import base64
from datetime import UTC, datetime, timedelta

import pytest
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows

from fiber_bundle import auth, database
from fiber_bundle import weixin_download as download
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_media as media
from fiber_bundle import weixin_recovery as recovery

URL = "https://novac2c.cdn.weixin.qq.com/c2c/download?synthetic=secret"


def captured(config, binding, item=None):
    recovery.initialize(config, binding)
    item = item or {"type": 4, "file_item": {"media": {"full_url": URL}}}
    job = inbox.receive(config, binding, "", poll(message(item_list=[item])))["events"][0]["job_id"]
    return job


def test_download_uses_original_descriptor_and_does_not_refetch(config, binding):
    job = captured(config, binding)
    calls = []

    async def fetch(url):
        calls.append(url)
        return b"synthetic bytes"

    first = asyncio.run(media.recover_item(config, binding, job, "0", fetcher=fetch))
    assert first["state"] == "pending" and first["dispatched"] is False
    assert asyncio.run(media.recover_item(config, binding, job, "0", fetcher=fetch)) == first
    assert calls == [URL] and rows(config, "media") == []
    assert rows(config, "wx_recovered_media")[0]["data"] == b"synthetic bytes"


def test_reference_media_item(config, binding):
    item = {
        "type": 1,
        "text_item": {"text": "reference"},
        "ref_msg": {"message_item": {"type": 3, "voice_item": {"media": {"full_url": URL}}}},
    }
    job = captured(config, binding, item)

    async def fetch(url):
        return b"voice bytes"

    assert (
        asyncio.run(media.recover_item(config, binding, job, "0.ref", fetcher=fetch))["state"]
        == "pending"
    )


def test_revocation_during_download_rejects_commit(config, binding):
    job = captured(config, binding)

    async def fetch(url):
        db = database.connect(config.state_root)
        auth.set_grant(
            db, binding.client_id, binding.vault_id, query=False, original=False, add=False
        )
        db.close()
        return b"bytes"

    with pytest.raises(auth.Denied):
        asyncio.run(media.recover_item(config, binding, job, "0", fetcher=fetch))
    assert rows(config, "wx_recovered_media") == []


@pytest.mark.parametrize("failure", [TimeoutError, OSError])
def test_failure_is_categorical_and_keeps_missing(config, binding, failure):
    job = captured(config, binding)

    async def fetch(url):
        raise failure(URL)

    with pytest.raises(download.DownloadError) as error:
        asyncio.run(media.recover_item(config, binding, job, "0", fetcher=fetch))
    assert "secret" not in str(error.value)
    assert recovery.view(config, binding, job)["state"] == "incomplete"


def test_cancellation_does_not_commit(config, binding):
    job = captured(config, binding)

    async def scenario():
        entered = asyncio.Event()

        async def fetch(url):
            entered.set()
            await asyncio.Future()

        task = asyncio.create_task(media.recover_item(config, binding, job, "0", fetcher=fetch))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert rows(config, "wx_recovered_media") == []


@pytest.mark.parametrize(
    "url",
    [
        "http://novac2c.cdn.weixin.qq.com/a",
        "https://127.0.0.1/a",
        "https://evil.invalid/a",
        "https://novac2c.cdn.weixin.qq.com.evil.invalid/a",
        "https://u:p@novac2c.cdn.weixin.qq.com/a",
        "https://novac2c.cdn.weixin.qq.com:444/a",
        URL + "#x",
        URL + "\n",
    ],
)
def test_disallowed_sources_rejected(url):
    with pytest.raises(download.DownloadError):
        download.validate_url(url)


def test_media_key_variants_and_strict_padding():
    crypto = pytest.importorskip("cryptography.hazmat.primitives.ciphers")
    key = b"k" * 16
    for encoded in [base64.b64encode(key).decode(), base64.b64encode(key.hex().encode()).decode()]:
        src = download.source(
            {
                "type": 4,
                "file_item": {"media": {"encrypt_query_param": "secret&x", "aes_key": encoded}},
            }
        )
        assert src.key == key and "secret%26x" in src.url and "secret" not in repr(src)
    src = download.source(
        {"type": 2, "image_item": {"aeskey": key.hex(), "media": {"full_url": URL}}}
    )
    encryptor = crypto.Cipher(crypto.algorithms.AES(key), crypto.modes.ECB()).encryptor()
    encrypted = encryptor.update(b"plain" + bytes([11]) * 11) + encryptor.finalize()
    assert download.decode(encrypted, src.key) == b"plain"
    with pytest.raises(download.DownloadError):
        download.decode(b"bad cipher", key)
    with pytest.raises(download.DownloadError):
        download.source({"type": 4, "file_item": {"media": {"encrypt_query_param": "secret"}}})


def test_real_tls_transport_limits_redirects_and_private_dns(
    tmp_path, monkeypatch, config, binding
):
    aiohttp = pytest.importorskip("aiohttp")
    import socket
    import ssl

    from aiohttp import web
    from aiohttp.resolver import ThreadedResolver
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    host = "novac2c.cdn.weixin.qq.com"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert = tmp_path / "synthetic.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private = tmp_path / "synthetic-key.pem"
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
    monkeypatch.setattr(aiohttp.connector, "_SSL_CONTEXT_VERIFIED", client_ssl)
    monkeypatch.setattr(download, "WIRE_LIMIT", 64)
    requests = []

    async def scenario():
        async def handler(request):
            requests.append(request.path)
            assert "Authorization" not in request.headers and "Cookie" not in request.headers
            if request.path == "/redirect":
                raise web.HTTPFound("https://evil.invalid/secret")
            if request.path == "/encoded":
                return web.Response(body=b"x", headers={"Content-Encoding": "gzip"})
            if request.path == "/large":
                return web.Response(body=b"x" * 65)
            if request.path == "/stream":
                response = web.StreamResponse()
                await response.prepare(request)
                await response.write(b"x" * 40)
                await response.write(b"x" * 40)
                await response.write_eof()
                return response
            return web.Response(body=b"synthetic body")

        app = web.Application()
        app.router.add_get("/{name}", handler)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ssl)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        async def resolve(self, hostname, port_arg=0, family=socket.AF_INET):
            return [
                {
                    "hostname": hostname,
                    "host": "127.0.0.1",
                    "port": port,
                    "family": socket.AF_INET,
                    "proto": 0,
                    "flags": 0,
                }
            ]

        monkeypatch.setattr(ThreadedResolver, "resolve", resolve)
        try:
            with pytest.raises(download.DownloadError):
                await download.fetch(f"https://{host}/ok")
            assert requests == []  # Production public-IP gate blocks the synthetic server.
            # Test-only routing override; production API has no switch to allow private IPs.
            monkeypatch.setattr(download, "public_address", lambda address: True)
            assert await download.fetch(f"https://{host}/ok") == b"synthetic body"
            for path in ["redirect", "encoded", "large", "stream"]:
                with pytest.raises(download.DownloadError):
                    await download.fetch(f"https://{host}/{path}")
            assert len(requests) == 5  # No redirect follow-up or retry.
            job = captured(
                config,
                binding,
                {"type": 4, "file_item": {"media": {"full_url": f"https://{host}/ok"}}},
            )
            result = await media.recover_item(config, binding, job, "0")
            assert result["state"] == "pending" and result["dispatched"] is False
            assert rows(config, "wx_recovered_media")[0]["data"] == b"synthetic body"
            assert len(requests) == 6
            # Exercise the scheduler through the same real local TLS transport.
            from fiber_bundle import weixin_batches, weixin_confirmations, weixin_media_dispatch

            inbox.receive(
                config,
                binding,
                inbox.cursor(config, binding),
                poll(
                    message(
                        "scheduled",
                        item_list=[
                            {"type": 4, "file_item": {"media": {"full_url": f"https://{host}/ok"}}}
                        ],
                    ),
                    cursor="scheduled",
                ),
            )
            weixin_confirmations.initialize(config, binding)
            weixin_confirmations.process(config, binding)
            weixin_batches.advance(config, binding, now=datetime.now(UTC) + timedelta(seconds=5))
            scheduled = await weixin_media_dispatch.drain(config, binding, maximum=1)
            assert scheduled["processed"] == 1
            assert scheduled["attempts"][0]["state"] == "succeeded"
            assert len(requests) == 7
            assert len(rows(config, "wx_recovered_media")) == 2
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
