"""Bounded process-local slow/busy request observations; no payloads or credentials."""

import re
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Request

from .config import Config
from .query_http import access


class Observations:
    def __init__(self) -> None:
        self.entries: deque[dict[str, Any]] = deque(maxlen=64)
        self.routes: list[tuple[Any, str, set[str]]] = []

    def register(self, routes: Any) -> None:
        """Retain public route templates so pre-handler rejection is attributable."""
        self.routes = [
            (route.path_regex, route.path, set(route.methods))
            for route in routes
            if getattr(route, "path_regex", None) is not None
            and getattr(route, "methods", None)
            and route.path.startswith(("/admin/", "/v1/", "/health"))
        ]

    def record(self, scope: Any, status: int, elapsed: float, timing: dict[str, Any]) -> None:
        if elapsed < 0.5 and status < 500:
            return
        route = getattr(scope.get("route"), "path", "")
        if not route:
            route = next(
                (
                    template
                    for pattern, template, methods in self.routes
                    if scope.get("method") in methods and pattern.fullmatch(scope.get("path", ""))
                ),
                "",
            )
        # Unknown routes are not copied: a URL can contain an arbitrary private value.
        if not route and scope.get("path") in {
            "/admin/vaults",
            "/admin/clients",
            "/admin/channels",
            "/admin/components",
            "/admin/backend",
            "/admin/batches/pending",
            "/admin/knowledge-graph/notes",
            "/admin/organization/workspace",
            "/admin/http-diagnostics",
        }:
            route = scope["path"]
        if not route or not route.startswith(("/admin/", "/v1/", "/health")):
            route = "/other"
        route = re.sub(r"\{[^}]+\}", "{id}", route)
        self.entries.append(
            {
                "observed_at": time.time(),
                "route": route,
                "method": scope["method"]
                if scope["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
                else "OTHER",
                "status": status,
                "duration_ms": round(elapsed * 1000, 1),
                "queue_ms": timing.get("queue_ms", 0),
                "storage_ms": timing.get("storage_ms", 0),
                "stage": timing.get("stage", "handler"),
                **(
                    {"display_phase": timing["display_phase"]}
                    if timing.get("display_phase") in {"capacity", "prepare", "compute", "deliver"}
                    else {}
                ),
            }
        )


def router(config: Config, observations: Observations) -> APIRouter:
    routes = APIRouter(prefix="/admin")

    @routes.get("/http-diagnostics")
    def diagnostics(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return {"scope": "current_process", "limit": 64, "requests": list(observations.entries)}

    return routes
