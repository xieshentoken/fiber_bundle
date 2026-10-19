"""Vault-scoped preservation evidence and private, verified recovery points."""

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

from .. import database, file_exchange
from ..config import Config, sync_directory
from ..inputs import canonical
from . import files
from .store import digest


def _rows(db: Any, vault: str, *, queue_tables: frozenset[str] = frozenset()) -> dict[str, Any]:
    out = {}
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        name = '"' + table.replace('"', '""') + '"'
        columns = {r[1] for r in db.execute("PRAGMA table_info(" + name + ")")}
        if table in {"vault_operations", "vault_directory_previews"} | queue_tables:
            continue
        clause = (
            "vault_id=?" if "vault_id" in columns else "vault=?" if "vault" in columns else None
        )
        if (
            clause is None
            and "source_id" in columns
            and db.execute("SELECT 1 FROM sqlite_master WHERE name='sources'").fetchone()
        ):
            clause = "source_id IN (SELECT id FROM sources WHERE vault_id=?)"
        if (
            clause is None
            and ({"source_version_id", "version_id"} & columns)
            and db.execute("SELECT 1 FROM sqlite_master WHERE name='source_versions'").fetchone()
        ):
            column = "source_version_id" if "source_version_id" in columns else "version_id"
            clause = (
                column
                + " IN (SELECT v.id FROM source_versions v JOIN sources s ON s.id=v.source_id "
                "WHERE s.vault_id=?)"
            )
        if clause is None:
            continue
        rows = [dict(r) for r in db.execute("SELECT * FROM " + name + " WHERE " + clause, (vault,))]
        # JSON text is kept byte-for-byte; blob values are represented by content digests.
        normalized = [
            {
                k: {"blob_sha256": digest_bytes(v)} if isinstance(v, bytes) else v
                for k, v in r.items()
            }
            for r in rows
        ]
        if normalized:
            out[table] = sorted(normalized, key=canonical)
    return out


def digest_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def preservation(config: Config, db: Any, vault: str) -> dict[str, Any]:
    out: dict[str, Any] = {"metadata.sqlite3": _rows(db, vault)}
    for path in sorted(config.state_root.glob("*.sqlite3")):
        if path.name == "metadata.sqlite3":
            continue
        info = path.lstat()
        if not path.is_file() or path.is_symlink() or info.st_nlink != 1:
            files.reject("checkpoint_invalid")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as side:
            side.row_factory = sqlite3.Row
            rows = _rows(
                side,
                vault,
                queue_tables=frozenset({"commands"})
                if path.name == "organization.sqlite3"
                else frozenset(),
            )
            if path.name == "message-channels.sqlite3":
                rows["channels"] = [
                    dict(r)
                    for r in side.execute(
                        "SELECT * FROM channels WHERE json_extract(value,'$.vault_id')=? "
                        "ORDER BY id",
                        (vault,),
                    )
                ]
            if rows:
                out[path.name] = rows
    return out


def summary(evidence: dict[str, Any]) -> dict[str, int]:
    main = evidence["metadata.sqlite3"]
    mocs = evidence.get("moc-associations.sqlite3", {})
    channels = evidence.get("message-channels.sqlite3", {})
    return {
        "sources": len(main.get("sources", [])),
        "mocs": len(mocs.get("mocs", [])),
        "channels": len(channels.get("channels", [])),
        "grants": len(main.get("client_vault_grants", [])),
    }


def checkpoint(config: Config, root: Path, plan: dict[str, Any], identity: str) -> dict[str, Any]:
    directory = config.state_root / "vault-relocations"
    if directory.is_symlink():
        files.reject("checkpoint_invalid")
    directory.mkdir(mode=0o700, exist_ok=True)
    home = directory / (identity + "-" + str(uuid4()))
    home.mkdir(mode=0o700)
    content = home / "files"
    content.mkdir(mode=0o700)
    for item in plan["files"]:
        target = content / item["path"]
        if item["kind"] == "directory":
            target.mkdir(mode=0o700)
            continue
        source = root / item["path"]
        parent_fd = file_exchange.parent_fd(root, item["path"])
        try:
            fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        import hashlib

        hashed = hashlib.sha256()
        with os.fdopen(fd, "rb") as inp, target.open("xb") as out:
            before = os.fstat(inp.fileno())
            if [before.st_dev, before.st_ino] != [item["dev"], item["ino"]]:
                files.reject("source_changed")
            target.chmod(0o600)
            remaining = item["bytes"]
            while remaining:
                chunk = inp.read(min(1024 * 1024, remaining))
                if not chunk:
                    files.reject("source_changed")
                hashed.update(chunk)
                out.write(chunk)
                remaining -= len(chunk)
            if inp.read(1) or hashed.hexdigest() != item["sha256"]:
                files.reject("source_changed")
            out.flush()
            os.fsync(out.fileno())
    databases = []
    database_hashes = {}
    with database.locked(config):
        for path in sorted(config.state_root.glob("*.sqlite3")):
            if path.is_symlink() or path.stat().st_nlink != 1:
                files.reject("checkpoint_invalid")
            dest = home / path.name
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source_db:
                with closing(sqlite3.connect(dest)) as target_db:
                    source_db.backup(target_db)
                    if target_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        files.reject("checkpoint_invalid")
            dest.chmod(0o600)
            with dest.open("rb") as stream:
                os.fsync(stream.fileno())
                database_hashes[path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
            databases.append(path.name)
    metadata = {
        "plan": plan,
        "databases": databases,
        "database_sha256": database_hashes,
        "storage": config.storage_configuration(),
    }
    manifest = home / "manifest.json"
    manifest.write_text(canonical(metadata))
    manifest.chmod(0o600)
    with manifest.open("rb") as stream:
        os.fsync(stream.fileno())
    for item in reversed(plan["files"]):
        if item["kind"] == "directory":
            sync_directory(content / item["path"])
    sync_directory(content)
    sync_directory(home)
    sync_directory(directory)
    return {
        "path": str(home),
        "manifest_sha256": digest(metadata),
        "databases": databases,
        "verified": True,
    }


def verify_checkpoint(value: dict[str, Any], plan: dict[str, Any]) -> None:
    """A saved boolean is not evidence: recheck recovery files before a move."""
    home = Path(value["path"])
    files.key(home)
    if home.is_symlink() or home.stat().st_mode & 0o077:
        files.reject("checkpoint_invalid")
    manifest = home / "manifest.json"
    if manifest.is_symlink():
        files.reject("checkpoint_invalid")
    try:
        metadata = json.loads(manifest.read_text())
    except (ValueError, UnicodeError):
        files.reject("checkpoint_invalid")
    if digest(metadata) != value["manifest_sha256"] or metadata["plan"] != plan:
        files.reject("checkpoint_invalid")
    expected = [
        {k: f[k] for k in ("path", "kind", "bytes", "sha256") if k in f} for f in plan["files"]
    ]
    actual = [
        {k: f[k] for k in ("path", "kind", "bytes", "sha256") if k in f}
        for f in files.inventory(home / "files")
    ]
    if actual != expected:
        files.reject("checkpoint_invalid")
    for name in value["databases"]:
        path = home / name
        if path.parent != home or path.is_symlink():
            files.reject("checkpoint_invalid")
        with path.open("rb") as stream:
            if (
                hashlib.file_digest(stream, "sha256").hexdigest()
                != metadata["database_sha256"][name]
            ):
                files.reject("checkpoint_invalid")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                files.reject("checkpoint_invalid")
