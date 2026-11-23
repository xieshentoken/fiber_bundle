"""Verify the real packaged HTTP route serves the complete new GUI dependency graph."""

import re
import socket
import threading
import urllib.request
from urllib.parse import urljoin, urlparse

import uvicorn
from fastapi import FastAPI

from fiber_bundle.web_routes import WEB_ROOT, router


def test_inbox_review_http_asset_dependency_closure():
    app = FastAPI()
    app.include_router(router())
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{port}/static/"
    pending = [
        urljoin(base, name)
        for name in (
            "organization_maintenance.js",
            "organization_api.js",
            "api.js",
            "style.css",
            "atlas/main.js",
            "atlas/feature_controls.css",
        )
    ]
    visited = set()
    try:
        while pending:
            address = pending.pop()
            if address in visited:
                continue
            with urllib.request.urlopen(address, timeout=10) as response:
                content = response.read()
                relative = urlparse(address).path.removeprefix("/static/")
                assert content == (WEB_ROOT / relative).read_bytes(), relative
                expected = "javascript" if relative.endswith(".js") else "css"
                assert expected in response.headers.get("content-type", ""), relative
            visited.add(address)
            source = content.decode()
            imports = re.findall(r"""(?:\bfrom\s*|\bimport\s*)["'](\.[^"']+)["']""", source)
            imports += re.findall(r"""@import\s+url\(["'](\.[^"']+)["']\)""", source)
            pending.extend(urljoin(address, dependency) for dependency in imports)
        assert {
            urljoin(base, name)
            for name in (
                "organization_inbox_review.js",
                "organization_inbox_review_view.js",
                "organization_inbox_review_api.js",
                "organization_inbox_review.css",
                "i18n/organization_inbox_review.js",
            )
        } <= visited
    finally:
        server.should_exit = True
        worker.join(timeout=10)
        listener.close()
        assert not worker.is_alive()
