"""One explicit refresh joins metadata reconciliation, package admission and index publication.

The private request is a restartable intent, not evidence that sources were indexed.
Reading its activity never schedules work. The existing workers retain all source/CAS guards.
"""

import json
import math
import sqlite3
import time
from typing import Any

from .. import auth, database, note_search_jobs
from ..config import Config, IntegrityError, make_directory
from ..inputs import canonical
from ..note_search_cache import location
from ..query_policy_store import write_private
from ..query_routing_files import read as private_read
from . import store


def read(config: Config, identity: str) -> dict[str, Any] | None:
    path = location(config, identity).with_suffix(".refresh.json")
    if not path.exists():
        return None
    raw = private_read(path, 4096, private=True)
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise IntegrityError("vault_sync_refresh_record_invalid") from error
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schema", "vault_id", "client_id", "job_id", "requested_at", "phase", "packages"}
        or value["schema"] != "vault-index-refresh/v1"
        or value["vault_id"] != identity
        or value["phase"] not in {"prepared", "queued", "blocked"}
        or type(value["packages"]) is not bool
        or type(value["requested_at"]) not in {int, float}
        or not math.isfinite(value["requested_at"])
        or value["requested_at"] <= 0
        or not all(isinstance(value[k], str) and value[k] for k in ("job_id", "client_id"))
        or canonical(value).encode() != raw
    ):
        raise IntegrityError("vault_sync_refresh_record_invalid")
    return value


def _write(config: Config, value: dict[str, Any]) -> None:
    path = location(config, value["vault_id"]).with_suffix(".refresh.json")
    make_directory(path.parent)
    write_private(path, canonical(value).encode())


def _current(config: Config, identity: str) -> dict[str, Any] | None:
    value = read(config, identity)
    job = note_search_jobs.read(config, identity)
    return value if value and job and value["job_id"] == job["id"] else None


def _queue(config: Config, db: sqlite3.Connection, value: dict[str, Any]) -> None:
    identity, client = value["vault_id"], value["client_id"]
    auth.grant(db, client, identity, "query")
    library = store.library(config, identity)
    if library is None:
        raise IntegrityError("vault_sync_unknown_library")
    # This is an explicit metadata reconciliation, split by the existing scanner.
    # It never reads source bodies and still filters protected names before stat.
    store.enqueue(config, identity, [("", 1)], library["cursor"])
    try:
        auth.grant(db, client, identity, "add")
    except auth.Denied:
        # Query-only clients may update an existing index, but cannot admit packages.
        value["packages"] = False
    else:
        from ..organization import package_discovery

        package_discovery.request(
            config, db, client, identity, notify=False, request_id=value["job_id"]
        )
        value["packages"] = True
    value["phase"] = "queued"
    _write(config, value)


def request(
    config: Config, db: sqlite3.Connection, client: str, identity: str, job_id: str
) -> None:
    """Caller holds the writer lease. Repeated clicks share one restartable intent."""
    current = _current(config, identity)
    if current and current["phase"] in {"prepared", "queued"}:
        try:
            auth.grant(db, current["client_id"], identity, "query")
            if current["packages"]:
                auth.grant(db, current["client_id"], identity, "add")
        except auth.Denied:
            pass  # Only a new explicit authorized click may replace a revoked intent owner.
        else:
            if current["phase"] == "prepared":
                _queue(config, db, current)
            return
    value = current or {
        "schema": "vault-index-refresh/v1",
        "vault_id": identity,
        "client_id": client,
        "job_id": job_id,
        "requested_at": time.time(),
        "phase": "prepared",
        "packages": False,
    }
    value.update(client_id=client, phase="prepared")
    # A crash between this intent and queue insertion can be resumed by advance.
    _write(config, value)
    _queue(config, db, value)


def advance(config: Config, identity: str) -> None:
    """Resume only an unqueued refresh intent; no classification or model work."""
    current = _current(config, identity)
    if current is None or current["phase"] != "prepared":
        return
    with database.locked(config), database.connect(config.state_root) as db:
        current = _current(config, identity)
        if current is not None and current["phase"] == "prepared":
            try:
                _queue(config, db, current)
            except auth.Denied:
                current["phase"] = "blocked"
                _write(config, current)


def activity(config: Config, identity: str) -> dict[str, str] | None:
    """Small read-only phase projection for the shared global status indicator."""
    value = _current(config, identity)
    if value is None:
        return None
    if value["phase"] == "blocked":
        return {"stage": "observation", "state": "waiting"}
    status = store.status(config, identity)
    if value["phase"] == "prepared" or status is None:
        return {"stage": "observation", "state": "queued"}
    if status["active_pending"]:
        return {"stage": "observation", "state": "observing"}
    if value["packages"]:
        from ..organization import package_discovery

        scan = package_discovery.status(config, value["client_id"], identity)
        if scan.get("request_id") != value["job_id"] or scan["state"] == "queued":
            return {"stage": "packages", "state": "queued"}
        if scan["state"] != "ready":
            return {"stage": "packages", "state": "waiting"}
    return {"stage": "index", "state": "ready"}


def ready(config: Config, identity: str) -> bool:
    value = activity(config, identity)
    return value is None or value["stage"] == "index"


def package_issues(config: Config, identity: str) -> list[dict[str, str]]:
    """A failed copied package is local coverage loss, never a fictional note issue."""
    value = _current(config, identity)
    if value is None or not value["packages"]:
        return []
    from ..organization import package_discovery

    scan = package_discovery.status(config, value["client_id"], identity)
    if scan.get("request_id") != value["job_id"]:
        return []
    return [
        {"path": item["path"], "reason": "expert_package_scan_failed"}
        for item in scan["items"]
        if item.get("state") == "failed"
    ]
