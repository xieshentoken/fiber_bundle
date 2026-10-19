"""Actual loopback server fixture, independent of production ports and data."""

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from fiber_bundle import auth
from fiber_bundle.api import create_app
from fiber_bundle.http_guard import Settings


@pytest.fixture
def network(config, store):
    auth.set_password(config, "synthetic-password")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(config, Settings(port=port))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    origin = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=origin, timeout=10, headers={"Origin": origin}) as client:
        login = client.post("/admin/login", json={"password": "synthetic-password"})
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        yield client
    server.should_exit = True
    thread.join(5)
    assert not thread.is_alive()
