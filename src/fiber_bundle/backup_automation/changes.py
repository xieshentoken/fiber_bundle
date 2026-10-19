"""Read-only logical business fingerprints; polling timestamps and backup files are excluded."""

import hashlib
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

from .. import database, vaults
from ..backup_preflight import DATABASES
from ..config import Config, InvalidInput, contained
from ..inputs import canonical
from ..vault_scope import entries, for_vault

# These are runtime/cache artifacts, not new knowledge or durable user choices.
SKIP_TABLES = {
    "admin_sessions",
    "admin_login_attempts",
    "login_attempts",
    "sessions",
    "vectors",
    "analysis_cache",
    "package_scans",
    "triggers",
    "runtime",
}
SKIP_COLUMNS = {
    "updated",
    "updated_at",
    "heartbeat",
    "heartbeat_at",
    "next_check",
    "checked_at",
    "last_seen",
    "last_used_at",
    "last_polled_at",
    "cursor",
    "previous_cursor",
    "last_digest",
    "last_result",
}


def observe(config: Config, cache: dict[str, Any] | None = None) -> dict[str, Any]:
    items: dict[str, list[Any]] = {}

    def add(key: str, digest: str, size: int) -> None:
        items[key] = [digest, size]
        if len(items) > 100000:
            raise InvalidInput("backup_observation_limit")

    for name in sorted(DATABASES):
        path = contained(config.state_root, name)
        if not path.exists():
            continue
        signatures: list[list[int] | None] = []
        for candidate in (path, Path(str(path) + "-wal")):
            try:
                info = candidate.stat()
                signatures.append(
                    [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
                )
            except FileNotFoundError:
                signatures.append(None)
        saved = cache.get(name) if cache is not None else None
        if saved is not None and saved["signatures"] == signatures:
            items.update(saved["items"])
            continue
        before_keys = set(items)
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in sorted(tables):
                if table.startswith("sqlite_") or table in SKIP_TABLES:
                    continue
                quoted = '"' + table.replace('"', '""') + '"'
                table_info = db.execute("PRAGMA table_info(" + quoted + ")").fetchall()
                columns = [r[1] for r in table_info if r[1] not in SKIP_COLUMNS]
                primary = [r[1] for r in table_info if r[5]]
                for row in db.execute("SELECT * FROM " + quoted):
                    data = {
                        key: (row[key].hex() if isinstance(row[key], bytes) else row[key])
                        for key in columns
                    }
                    # Receiver connection state has frequent transport-only changes.
                    if name == "message-channels.sqlite3" and table == "channels":
                        data.pop("state", None)
                    wire = canonical(data).encode()
                    key = canonical([row[k] for k in primary]) if primary else wire.hex()
                    key = hashlib.sha256(key.encode()).hexdigest()
                    add(
                        "db:" + name + ":" + table + ":" + key,
                        hashlib.sha256(wire).hexdigest(),
                        len(wire),
                    )
        if cache is not None:
            cache[name] = {
                "signatures": signatures,
                "items": {key: items[key] for key in items.keys() - before_keys},
            }
    with database.connect(config.state_root, readonly=True) as db:
        for row in db.execute("SELECT * FROM vaults ORDER BY id"):
            if row["attachment_state"] in {"detached", "trashed"}:
                continue
            root = vaults.checked_vault(config, row, pending=True, metadata=db)
            scope = for_vault(root, row)
            for path, info in entries(root, scope, maximum=100000, hidden=True):
                if not path.is_file():
                    continue
                key = "vault:" + row["id"] + ":" + path.relative_to(root).as_posix()
                add(
                    key,
                    str(info.st_mtime_ns) + ":" + str(info.st_ino) + ":" + str(info.st_ctime_ns),
                    info.st_size,
                )
    # Account for new source originals and processing artifacts, not just DB file size.
    skip = {
        "runtime",
        "browser-profiles",
        "message-channel-secrets",
        "agent-connection-secrets",
        "note-search",
        "execution-locks",
        "graph-locks",
        "deployment-backups",
        "m4-acceptance",
        "m5-acceptance",
        "m6-acceptance",
        "m8-acceptance",
        "m8-runtime",
    }
    count = 0
    for parent, directories, files in os.walk(config.state_root, followlinks=False):
        relative = Path(parent).relative_to(config.state_root)
        if len(relative.parts) > 32:
            raise InvalidInput("backup_depth_limit")
        directories[:] = [
            name
            for name in directories
            if not (not relative.parts and name in skip)
            and not (str(relative) == "configs" and name.startswith("hermes-feishu-"))
        ]
        for name in [*directories, *files]:
            count += 1
            if count > 100000:
                raise InvalidInput("backup_observation_limit")
            path = contained(config.state_root, (relative / name).as_posix())
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise InvalidInput("backup_requires_independent_regular_files")
            if (
                not stat.S_ISREG(info.st_mode)
                or name.endswith(
                    (".lock", ".ticket", ".sqlite3", "-wal", "-shm", "-journal", ".log", ".pyc")
                )
                or name in {".DS_Store", "backend.json"}
            ):
                continue
            add(
                "state:" + (relative / name).as_posix(),
                str(info.st_mtime_ns) + ":" + str(info.st_ctime_ns),
                info.st_size,
            )
    wire = canonical(items).encode()
    if len(wire) > 8 * 1024**2:
        raise InvalidInput("backup_observation_limit")
    return {"sha256": hashlib.sha256(wire).hexdigest(), "items": items}


def growth(previous: Any, current: Any) -> int:
    old = previous["items"] if previous else {}
    return sum(max(0, row[1] - old.get(key, [None, 0])[1]) for key, row in current["items"].items())
