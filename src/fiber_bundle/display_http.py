"""Buffer expensive display reads outside the lease; reauthorize before delivery.

Only explicitly audited GET projections use this path. The storage generation and
observed source files must still match under the delivery lease. Nothing is cached
across sessions or reused by preview, confirmation, query execution or writeback.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import auth, database, display_observation
from .config import Config, IntegrityError, InvalidInput, StorageError
from .http_admission import dispatch

PATHS = frozenset(
    {
        "/admin/organization",
        "/admin/vault-placements",
        "/admin/vault-placements/mocs",
        "/admin/knowledge-graph/notes",
        "/admin/note-index/status",
    }
)


def stamp(config: Config, request: Request) -> dict[str, Any]:
    """Cheap local generations, including missing policy paths and sidecar journals."""
    root = config.state_root
    # These audited projections read permissions, publications, MOC/organization
    # ledgers and the live note index, never the message transport ledger.
    # Transport polling is independent of their result and must not invalidate it.
    unrelated = (
        {
            "message-channels.sqlite3",
            "message-channels.sqlite3-wal",
            "message-channels.sqlite3-journal",
            "organization.sqlite3",
            "organization.sqlite3-wal",
            "organization.sqlite3-journal",
        }
        if request.url.path in PATHS
        else set()
    )
    paths = [
        p
        for p in root.iterdir()
        if p.name not in unrelated
        and p.name.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-journal", ".json"))
    ]
    for name in (
        "configs",
        "query-policies",
        "model-secrets",
        "moc-files",
        "moc-note-files",
        "note-search",
    ):
        directory = root / name
        if directory.exists():
            paths.extend(
                p
                for p in (directory.iterdir() if name == "configs" else directory.rglob("*"))
                if p.is_file()
                and not (
                    name == "note-search"
                    and p.name.endswith(
                        (".building", ".building-journal", ".building-wal", ".building-shm")
                    )
                )
            )
    with database.connect(root, readonly=True) as db:
        auth.admin(db, request.cookies.get(auth.COOKIE, ""))
        vault_id = request.query_params.get("vault_id")
        row = db.execute("SELECT * FROM vaults WHERE id=?", (vault_id,)).fetchone()
        if row is not None:
            from .vault_catalog import physical_path

            vault_root = physical_path(config, row)
            paths.extend(
                parent / ".knowledge-organizer-policy.json"
                for parent in [*vault_root.parents, vault_root]
            )
    if len(paths) > 20000:
        raise IntegrityError("display_observation_limit")
    result: dict[str, Any] = {}
    for path in paths:
        try:
            result[str(path)] = display_observation.signature(path)
        except FileNotFoundError:
            result[str(path)] = None
    from .organization import store

    with store.connection(config) as ledger:
        if ledger is not None:
            tables = {
                r[0] for r in ledger.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            result["organization_business"] = {
                table: [
                    tuple(row)
                    for row in ledger.execute(f"SELECT id,updated FROM {table} ORDER BY id")
                ]
                for table in sorted((store.TABLES & tables) - {"jobs", "package_scans"})
            }
            if "jobs" in tables:
                result["organization_intents"] = [
                    (
                        row[0],
                        value.get("generation"),
                        value.get("plan_sha256"),
                        value.get("confirmed"),
                    )
                    for row in ledger.execute("SELECT id,value FROM jobs ORDER BY id")
                    for value in [json.loads(row[1])]
                ]
    return result


async def respond(
    app: Any,
    serial: Any,
    limiter: Any,
    config: Config,
    scope: Any,
    receive: Any,
    send: Any,
    timing: dict[str, Any],
) -> None:
    request = Request(scope)

    async def admitted(handler: Any) -> None:
        await dispatch(
            handler, serial, config, scope, receive, send, timing, busy_code="display_busy"
        )

    before = None
    messages: list[Any] = []
    observations: dict[Path, tuple[int, ...]] = {}
    delivery_started = False

    async def error(code: str, status: int = 503) -> None:
        await JSONResponse({"error": code}, status, headers={"retry-after": "1"})(
            scope, receive, send
        )

    async def prepare(*_: Any) -> None:
        nonlocal before
        timing["display_phase"] = "prepare"
        before = await anyio.to_thread.run_sync(stamp, config, request)

    async def deliver(*_: Any) -> None:
        nonlocal delivery_started
        # Authorization is checked again, including expiry, even if no file changed.
        after = await anyio.to_thread.run_sync(stamp, config, request)
        if before != after:
            timing["stage"] = "display_changed"
            await error("display_changed")
            return
        try:
            await anyio.to_thread.run_sync(display_observation.verify, observations)
        except FileNotFoundError:
            timing["stage"] = "display_changed"
            await error("display_changed")
            return
        from .note_search_jobs import changed

        for vault_id in pending_indexes:
            await anyio.to_thread.run_sync(changed, config, vault_id)
        for message in messages:
            delivery_started = True
            await send(message)

    pending_indexes: set[str] = set()
    size = 0

    async def capture(message: Any) -> None:
        nonlocal size
        size += len(message.get("body", b""))
        if size > 32 * 1024 * 1024:
            raise IntegrityError("display_response_limit")
        messages.append(message)

    acquired = False
    try:
        timing["display_phase"] = "capacity"
        try:
            with anyio.fail_after(1):
                await limiter.acquire()
            acquired = True
        except TimeoutError:
            timing["stage"] = "display_capacity"
            await error("display_busy")
            return
        await admitted(prepare)
        if before is None:
            return
        token = display_observation.current.set(observations)
        pending_token = display_observation.pending_indexes.set(pending_indexes)
        try:
            timing["display_phase"] = "compute"
            await app(scope, receive, capture)
        finally:
            display_observation.current.reset(token)
            display_observation.pending_indexes.reset(pending_token)
        timing["display_phase"] = "deliver"
        await admitted(deliver)
    except auth.Denied as exc:
        await error("request_denied", exc.status)
    except (StorageError, OSError, sqlite3.Error) as exc:
        if delivery_started:
            raise  # A disconnected response cannot be replaced with another HTTP response.
        code = display_observation.error_code(exc) or (
            "display_busy"
            if isinstance(exc, database.LockTimeout)
            else "invalid_input"
            if isinstance(exc, InvalidInput)
            else "storage_unavailable"
        )
        timing["stage"] = code
        await error(code, 422 if code == "invalid_input" else 503)
    finally:
        if acquired:
            limiter.release()
