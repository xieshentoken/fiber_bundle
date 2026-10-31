"""Bounded metadata reconciliation. Only named changed directories are enumerated."""

import json
import os
import stat
import time
from pathlib import Path
from typing import Any

from .. import cloud_files
from ..config import Config, IntegrityError
from ..inputs import canonical
from . import scope_guard, store

SLICE = 100


def signature(info: os.stat_result) -> str:
    return canonical(
        [
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            getattr(info, "st_birthtime", 0),
            getattr(info, "st_flags", 0),
        ]
    )


def apply(config: Config, task: dict[str, Any], *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    row = store.library(config, task["vault"])
    if row is None:
        return
    root = Path(row["root"])
    scope = scope_guard.current(config, row)
    with store.connection(config) as side:
        prior_root = (
            side.execute(
                "SELECT signature FROM files WHERE vault=? AND path=''", (task["vault"],)
            ).fetchone()
            if side
            else None
        )
    if prior_root and json.loads(prior_root[0])[:2] != [root.lstat().st_dev, root.lstat().st_ino]:
        raise IntegrityError("vault_sync_root_changed")
    relative = task["path"]
    if not store.accepted(scope, relative):
        raise IntegrityError("vault_sync_scope_changed")
    path = root / relative
    # Check ancestors locally; placeholders must never be materialized just to observe changes.
    cloud_files.require_local(path.parent if relative else root)
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and stat.S_ISLNK(info.st_mode):
        raise IntegrityError("vault_sync_symlink")
    observations: dict[str, tuple[str, int, int]] = {}
    child_tasks: list[tuple[str, int]] = []
    direct: set[str] = set()
    directory = info is not None and stat.S_ISDIR(info.st_mode)
    scans = stats = 0
    if info is not None:
        cloud_files.require_local(path)
        # Metadata listing does not require every child to have been downloaded.
        observations[relative] = (signature(info), info.st_size, int(directory))
        stats += 1
    finished = True
    position = 0
    names: list[str] = []
    if directory:
        assert info is not None
        with store.connection(config) as side:
            saved = (
                side.execute(
                    "SELECT * FROM directory_scans WHERE vault=? AND path=?",
                    (task["vault"], relative),
                ).fetchone()
                if side
                else None
            )
        if saved and saved["signature"] == signature(info):
            names = json.loads(saved["names"])
            position = saved["position"]
        else:
            scans = 1
            with os.scandir(path) as entries:
                for entry in entries:
                    child = (relative + "/" if relative else "") + entry.name
                    if not store.accepted(scope, child):
                        continue
                    if child.count("/") > 32 or len(names) >= 10000:
                        raise IntegrityError("vault_sync_directory_limit")
                    names.append(child)
            names.sort()
        direct = set(names)
        for child in names[position : position + SLICE]:
            # Filtered names are persisted before any child stat; no protected path enters here.
            item = (root / child).lstat()
            stats += 1
            if stat.S_ISLNK(item.st_mode):
                continue
            is_dir = stat.S_ISDIR(item.st_mode)
            observations[child] = (signature(item), item.st_size, int(is_dir))
            if is_dir and task["flags"] & 1:
                child_tasks.append((child, 1))
        position += SLICE
        finished = position >= len(names)
        if signature(path.lstat()) != signature(info):
            raise IntegrityError("vault_sync_source_changed")
    with store.connection(config, write=True) as db:
        assert db is not None
        current = db.execute(
            "SELECT * FROM pending WHERE vault=? AND path=?", (task["vault"], relative)
        ).fetchone()
        if current is None or current["revision"] != task["revision"]:
            return
        old = {
            r["path"]: dict(r)
            for r in db.execute("SELECT * FROM files WHERE vault=?", (task["vault"],))
        }
        removed = []
        for name in old:
            if name == relative or name.startswith(relative + "/") or relative == "":
                immediate = name.rsplit("/", 1)[0] if "/" in name else ""
                if info is None or (
                    finished
                    and directory
                    and immediate == relative
                    and name not in direct
                    and name != relative
                ):
                    removed.append(name)
        changed = [
            (p, v) for p, v in observations.items() if p not in old or old[p]["signature"] != v[0]
        ]
        generation = db.execute(
            "SELECT generation FROM libraries WHERE id=?", (task["vault"],)
        ).fetchone()[0] + bool(changed or removed)
        for name in removed:
            db.execute(
                "DELETE FROM files WHERE vault=? AND (path=? OR substr(path,1,?)=?)",
                (task["vault"], name, len(name) + 1, name + "/"),
            )
            db.execute(
                "INSERT OR IGNORE INTO revisions VALUES(?,?,?,?,NULL,?)",
                (task["vault"], name, generation, old[name]["signature"], task["flags"]),
            )
        for name, (observed, size, directory_flag) in changed:
            db.execute(
                "INSERT INTO files VALUES(?,?,?,?,?,?) ON CONFLICT(vault,path) DO UPDATE SET "
                "signature=excluded.signature,size=excluded.size,directory=excluded.directory,"
                "revision=excluded.revision",
                (task["vault"], name, observed, size, directory_flag, generation),
            )
            db.execute(
                "INSERT OR IGNORE INTO revisions VALUES(?,?,?,?,?,?)",
                (
                    task["vault"],
                    name,
                    generation,
                    old.get(name, {}).get("signature"),
                    observed,
                    task["flags"],
                ),
            )
            if directory_flag and name not in old and name != relative:
                child_tasks.append((name, 1))
        for name, flags in child_tasks:
            db.execute(
                "INSERT INTO pending VALUES(?,?,?,?,?,0) ON CONFLICT(vault,path) DO UPDATE SET "
                "flags=flags|excluded.flags",
                (task["vault"], name, flags, generation, now),
            )
        if finished:
            db.execute(
                "DELETE FROM directory_scans WHERE vault=? AND path=?", (task["vault"], relative)
            )
            db.execute(
                "DELETE FROM pending WHERE vault=? AND path=? AND revision=?",
                (task["vault"], relative, task["revision"]),
            )
        else:
            assert info is not None
            db.execute(
                "INSERT INTO directory_scans VALUES(?,?,?,?,?) "
                "ON CONFLICT(vault,path) DO UPDATE SET signature=excluded.signature,"
                "names=excluded.names,position=excluded.position",
                (task["vault"], relative, signature(info), canonical(names), position),
            )
            db.execute(
                "UPDATE pending SET due=? WHERE vault=? AND path=? AND revision=?",
                (now + 0.25, task["vault"], relative, task["revision"]),
            )
        pending = db.execute(
            "SELECT count(*) FROM pending WHERE vault=? AND attempts=0", (task["vault"],)
        ).fetchone()[0]
        db.execute(
            "UPDATE libraries SET generation=MAX(generation,?),checked_at=?,"
            "scans=scans+?,stats=stats+?,state=?,error=NULL,"
            "baseline=CASE WHEN ?=0 THEN 1 ELSE baseline END,"
            "last_audit=CASE WHEN ?=0 AND baseline=0 THEN ? ELSE last_audit END WHERE id=?",
            (
                generation,
                now,
                scans,
                stats,
                "pending" if pending else "index_pending",
                pending,
                pending,
                now,
                task["vault"],
            ),
        )


def heartbeat(config: Config, identity: str, *, now: float | None = None) -> None:
    with store.connection(config, write=True) as db:
        assert db is not None
        db.execute(
            "UPDATE libraries SET heartbeat=? WHERE id=?",
            (time.time() if now is None else now, identity),
        )


def fingerprint(config: Config, identity: str) -> str:
    """Stable metadata digest for backup triggers; no filesystem access to the Vault."""
    import hashlib

    with store.connection(config) as db:
        if db is None:
            raise IntegrityError("vault_sync_not_ready")
        row = db.execute("SELECT baseline FROM libraries WHERE id=?", (identity,)).fetchone()
        if not row or not row[0]:
            raise IntegrityError("vault_sync_not_ready")
        items = [
            list(r)
            for r in db.execute(
                "SELECT path,signature,size FROM files WHERE vault=? AND directory=0 ORDER BY path",
                (identity,),
            )
        ]
    return hashlib.sha256(canonical(items).encode()).hexdigest()
