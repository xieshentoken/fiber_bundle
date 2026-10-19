import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from test_api import add_client, payload, permissions, vault_ids

from fiber_bundle import database
from fiber_bundle.api import create_app
from fiber_bundle.config import InvalidInput
from fiber_bundle.http_guard import Settings


def test_body_limit_before_decode_chunked_and_peer(config, store):
    app = create_app(config, Settings(max_body_bytes=1024))

    async def run():
        output = []
        chunks = iter(
            [
                {"type": "http.request", "body": b"x" * 700, "more_body": True},
                {"type": "http.request", "body": b"x" * 700, "more_body": False},
            ]
        )

        async def receive():
            return next(chunks)

        async def send(message):
            output.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "http",
            "method": "POST",
            "path": "/v1/receipts",
            "raw_path": b"/v1/receipts",
            "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:8765")],
            "client": ("127.0.0.1", 4000),
            "server": ("127.0.0.1", 8765),
        }
        await app(scope, receive, send)
        assert output[0]["status"] == 413
        output.clear()
        scope["client"] = ("192.0.2.5", 4000)
        await app(scope, receive, send)
        assert output[0]["status"] == 400

    asyncio.run(run())


def test_response_delivery_serializes_revocation(http, config):
    cid, headers = add_client(http)
    vid = vault_ids(http)["personal"]
    permissions(http, cid, vid, True, True, True)
    http.post("/v1/receipts", headers=headers, json=payload(vid))
    version = http.get("/v1/vaults/" + vid + "/versions", headers=headers).json()[0]
    path = (
        "/v1/vaults/" + vid + "/versions/" + version["id"] + "/assets/" + version["assets"][0]["id"]
    )
    app = create_app(config)

    async def exercise():
        delivered = asyncio.Event()
        finish = asyncio.Event()
        started = threading.Event()
        revoked = threading.Event()

        def revoke():
            started.set()
            with database.locked(config):
                db = database.connect(config.state_root)
                db.execute("UPDATE clients SET active=0 WHERE id=?", (cid,))
                db.close()
            revoked.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [
                (b"host", b"127.0.0.1:8765"),
                (b"authorization", headers["Authorization"].encode()),
            ],
            "client": ("127.0.0.1", 4000),
            "server": ("127.0.0.1", 8765),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body":
                delivered.set()
                await finish.wait()

        request = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(delivered.wait(), 5)
        thread = threading.Thread(target=revoke)
        thread.start()
        assert await asyncio.to_thread(started.wait, 2)
        assert not await asyncio.to_thread(revoked.wait, 0.15)
        finish.set()
        await request
        await asyncio.to_thread(thread.join, 3)
        assert revoked.is_set()

    asyncio.run(exercise())
    assert http.get(path, headers=headers).status_code == 401


def test_original_buffer_limit(http, config):
    from fastapi.testclient import TestClient

    cid, headers = add_client(http)
    vid = vault_ids(http)["personal"]
    permissions(http, cid, vid, True, True, True)
    http.post("/v1/receipts", headers=headers, json=payload(vid))
    version = http.get("/v1/vaults/" + vid + "/versions", headers=headers).json()[0]
    path = (
        "/v1/vaults/" + vid + "/versions/" + version["id"] + "/assets/" + version["assets"][0]["id"]
    )
    with TestClient(
        create_app(config, Settings(max_original_bytes=1)),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 4000),
    ) as client:
        assert client.get(path, headers=headers).status_code == 413


def test_live_loopback_nonloopback_and_concurrent_requests(config, store):
    for host in ("0.0.0.0", "::", "localhost", "192.0.2.1"):
        with pytest.raises(InvalidInput):
            Settings(host=host).validate()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"),
    }
    command = [
        sys.executable,
        "-m",
        "fiber_bundle.cli",
        "serve",
        "--project-root",
        str(config.project_root),
        "--config",
        "config.json",
        "--port",
        str(port),
    ]
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    origin = "http://127.0.0.1:" + str(port)
    try:
        with httpx.Client(trust_env=False, timeout=3) as client:
            for _ in range(80):
                try:
                    if client.get(origin + "/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert process.poll() is None
                time.sleep(0.05)
            else:
                pytest.fail("listener did not start")
            assert client.get(origin + "/").status_code == 200
            assert (
                client.get(origin + "/health", headers={"Host": "evil.example"}).status_code == 400
            )
            with ThreadPoolExecutor(max_workers=48) as pool:
                statuses = list(
                    pool.map(lambda _: client.get(origin + "/health").status_code, range(60))
                )
            assert statuses == [200] * 60
            # A default-route UDP probe can select a VPN tunnel whose proxy accepts
            # TCP without delivering HTTP. Inspect real local non-tunnel interfaces.
            interfaces = subprocess.check_output(["ifconfig", "-a"], text=True)
            addresses = []
            current = ""
            for line in interfaces.splitlines():
                if line and not line[0].isspace():
                    current = line.split(":")[0]
                match = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)", line)
                if match and not current.startswith(("lo", "utun")):
                    addresses.append(match.group(1))
            assert addresses, "a local non-loopback interface is required for this acceptance"
            for local in addresses:
                with socket.socket() as remote:
                    remote.settimeout(1)
                    assert remote.connect_ex((local, port)) != 0
            listeners = subprocess.check_output(
                ["lsof", "-nP", "-a", "-p", str(process.pid), "-iTCP", "-sTCP:LISTEN"],
                text=True,
            )
            assert "127.0.0.1:" + str(port) in listeners
            assert "*:" + str(port) not in listeners
            response = client.post(
                origin + "/admin/login",
                json={"password": "synthetic-sensitive-marker"},
                headers={"Origin": origin},
            )
            assert response.status_code == 401
    finally:
        process.terminate()
        log = process.communicate(timeout=10)[0].decode()
    assert "synthetic-sensitive-marker" not in log and "Traceback" not in log
    assert "Uvicorn running on http://127.0.0.1:" in log


def test_password_unicode_and_corrupt_parameters_fail_safely(http, config):
    response = http.post("/admin/login", content=json.dumps({"password": "\ud800"}))
    assert response.status_code == 422
    db = database.connect(config.state_root)
    db.execute("UPDATE admin_auth SET n=99999999")
    db.close()
    assert (
        http.post("/admin/login", json={"password": "synthetic-test-password-only"}).status_code
        == 503
    )


def test_password_cli_requires_hidden_interactive_input(config, store):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fiber_bundle.cli",
            "set-password",
            "--project-root",
            str(config.project_root),
            "--config",
            "config.json",
        ],
        input="synthetic-password-must-not-echo",
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"),
        },
        timeout=5,
    )
    assert result.returncode == 1
    assert "synthetic-password" not in result.stdout + result.stderr
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM admin_auth").fetchone()[0] == 0
    db.close()
