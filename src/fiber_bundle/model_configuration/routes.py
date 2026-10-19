"""Administrator model APIs; credentials stay on the host and mutations require CSRF."""

import asyncio
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Request

from .. import auth, database
from ..config import Config
from . import discovery, providers, runtime, store, testing
from .contracts import Change, Connection, Save, Test
from .errors import ModelError


def router(config: Config, tests: testing.Runtime) -> APIRouter:
    routes = APIRouter(prefix="/admin/models")

    @contextmanager
    def access(request: Request) -> Iterator[sqlite3.Connection]:
        with database.connect(config.state_root) as db:
            auth.admin(
                db,
                request.cookies.get(auth.COOKIE, ""),
                request.headers.get("x-csrf-token", "") if request.method != "GET" else None,
            )
            yield db

    @routes.get("")
    def listing(request: Request) -> dict[str, Any]:
        with access(request) as db:
            return {
                **store.inspect(db),
                "providers": [
                    {"id": k, "name": v[0], "base_url": v[1], "protocol": v[2]}
                    for k, v in providers.PRESETS.items()
                ],
            }

    @routes.post("/discover")
    async def discover(data: Connection, request: Request) -> dict[str, Any]:
        with access(request):
            pass
        if await request.is_disconnected():
            raise ModelError("model_transport_failure")
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(discovery.discover, config, data), timeout=25
            )
        except TimeoutError:
            raise ModelError("model_timeout") from None
        if await request.is_disconnected():
            raise ModelError("model_transport_failure")
        return result

    @routes.get("/runtime")
    def environment(request: Request) -> dict[str, Any]:
        with access(request):
            pi, creation = runtime.inspect(), runtime.inspect("creation")
            state = (
                pi if pi["state"] != "ready" else creation if creation["state"] != "ready" else pi
            )
            return {
                **state,
                "capabilities": {"pi": pi, "creation": creation},
                "preparation": runtime.preparation(config),
            }

    @routes.post("/runtime/prepare")
    def prepare_environment(request: Request) -> dict[str, Any]:
        with access(request):
            if runtime.preparation(config)["state"] not in {"queued", "running"}:
                runtime.mark(config, "queued")
                tests.pool.submit(runtime.prepare, config)
            return runtime.preparation(config)

    @routes.post("")
    def save(data: Save, request: Request) -> dict[str, Any]:
        with access(request) as db:
            return store.save(config, db, data)

    @routes.post("/{identity}/control")
    def control(identity: str, data: Change, request: Request) -> dict[str, Any]:
        with access(request) as db:
            return store.change(db, identity, data)

    @routes.post("/{identity}/test")
    def test_model(identity: str, data: Test, request: Request) -> dict[str, Any]:
        with access(request) as db:
            return tests.start(db, identity, data)

    @routes.get("/tests/{identity}")
    def test_status(identity: str, request: Request) -> dict[str, Any]:
        with access(request) as db:
            return testing.status(db, identity)

    return routes
