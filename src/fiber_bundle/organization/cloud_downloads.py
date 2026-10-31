"""Durable deduplication for explicit native download requests, including unknown outcomes."""

import time
from collections.abc import Callable
from typing import Any

from ..inputs import canonical
from ..objects import digest
from . import store


def send(config: Any, vault: str, path: str, token: str, invoke: Callable[[], Any]) -> Any:
    key = digest(canonical([vault, path, token]).encode())
    prior = store.get(config, "cloud_downloads", key)
    if prior and prior["state"] in {"requested", "unknown", "downloading"}:
        return {"state": prior["state"], "requested_now": False}
    value = {"id": key, "path": path, "state": "unknown", "requested_at": time.time()}
    store.put(config, "cloud_downloads", key, vault, value)
    # Persist uncertainty before crossing the native boundary. A retry inspects
    # actual system status first and never silently resends an unknown request.
    result = invoke()
    value.update(state=result["state"])
    store.put(config, "cloud_downloads", key, vault, value)
    return result


def observed(config: Any, vault: str, path: str, token: str, state: str) -> None:
    if state not in {"available", "error", "conflict"}:
        return
    key = digest(canonical([vault, path, token]).encode())
    prior = store.get(config, "cloud_downloads", key)
    if prior and prior["state"] != state:
        prior.update(state=state, observed_at=time.time())
        store.put(config, "cloud_downloads", key, vault, prior)
