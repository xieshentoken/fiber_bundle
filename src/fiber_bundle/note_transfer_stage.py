"""Owned target staging, copy-only attachments, and exclusive original-note quarantine."""

import hashlib
import json
import math
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import database, file_exchange, publication_files
from . import note_migration_store as journal
from . import note_transfer_intent as intent
from . import note_transfer_links as links
from .config import Config, IntegrityError, contained
from .inputs import canonical


def names(execution: str, value: dict[str, Any]) -> tuple[str, str, str]:
    return (
        ".fiber-transfer-" + execution,
        ".fiber-moved-" + execution + ".md",
        value["transfer"]["attachment_folder"],
    )


def save(config: Config, execution: str, value: dict[str, Any], parent: Path) -> dict[str, Any]:
    stage, _, _ = names(execution, value)
    private = intent.files(config, execution, value)
    files = {
        "candidate.md": private["candidate.md"],
        **{k: v for k, v in private.items() if k.startswith("assets/")},
    }
    publication_files.stage(parent, stage, files)
    root = contained(parent, stage)
    info = root.stat()
    assets = contained(root, "assets") if any(k.startswith("assets/") for k in files) else None
    record = {
        "schema": 1,
        "dir_key": [info.st_dev, info.st_ino],
        "assets_key": [assets.stat().st_dev, assets.stat().st_ino] if assets else None,
        "files": {name: links.read(root, name)[1] for name in sorted(files)},
    }
    raw = canonical(record)
    with journal.connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            db.execute(
                "CREATE TABLE IF NOT EXISTS stages(execution_id TEXT PRIMARY KEY,"
                "record_json TEXT NOT NULL,record_sha256 TEXT NOT NULL) STRICT"
            )
            db.execute(
                "INSERT INTO stages VALUES(?,?,?)",
                (execution, raw, hashlib.sha256(raw.encode()).hexdigest()),
            )
    return record


def load(config: Config, execution: str, value: dict[str, Any]) -> dict[str, Any] | None:
    with journal.connection(config) as db:
        if (
            db is None
            or not db.execute("SELECT 1 FROM sqlite_master WHERE name='stages'").fetchone()
        ):
            return None
        row = db.execute("SELECT * FROM stages WHERE execution_id=?", (execution,)).fetchone()
    if row is None:
        return None
    raw = row["record_json"]
    if (
        len(raw.encode()) > 65536
        or hashlib.sha256(raw.encode()).hexdigest() != row["record_sha256"]
    ):
        raise IntegrityError("transfer staging identity digest changed")
    record = json.loads(raw)
    wanted = {"candidate.md"} | {
        "assets/" + a["target_name"] for a in value["transfer"]["attachments"]
    }
    if (
        not isinstance(record, dict)
        or set(record) != {"schema", "dir_key", "assets_key", "files"}
        or type(record["schema"]) is not int
        or record["schema"] != 1
        or canonical(record) != raw
        or not isinstance(record["files"], dict)
        or set(record["files"]) != wanted
    ):
        raise IntegrityError("invalid transfer staging identity scope")
    keys = [(record["dir_key"], 2)] + [(key, 3) for key in record["files"].values()]
    if len(wanted) > 1:
        keys.append((record["assets_key"], 2))
    elif record["assets_key"] is not None:
        raise IntegrityError("unexpected staging attachment directory")
    for key, length in keys:
        if (
            not isinstance(key, list)
            or len(key) != length
            or any(type(v) is not int or v < 0 for v in key[:2])
        ):
            raise IntegrityError("invalid staging physical key")
        if length == 3:
            birth = float.fromhex(key[2])
            if not math.isfinite(birth) or birth.hex() != key[2]:
                raise IntegrityError("invalid staging birth")
    return record


def matches(fd: int, name: str, key: list[Any], sha: str) -> bool:
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    # Attachments can be binary; use the same no-follow, independent-file bound.
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, "rb") as stream:
        first = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(first.st_mode)
            or first.st_nlink != 1
            or not 0 < first.st_size <= links.MAX_ATTACHMENT_BYTES
        ):
            raise IntegrityError("transfer file is not a bounded independent regular file")
        payload = stream.read(links.MAX_ATTACHMENT_BYTES + 1)
        after = os.fstat(stream.fileno())
        birth = getattr(first, "st_birthtime", None)
        actual = [first.st_dev, first.st_ino, float(birth if birth is not None else 0).hex()]
        if (
            actual != key
            or hashlib.sha256(payload).hexdigest() != sha
            or len(payload) != first.st_size
            or (first.st_size, first.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            or (info.st_dev, info.st_ino) != (first.st_dev, first.st_ino)
        ):
            raise IntegrityError("transfer physical file changed; preserve all versions")
    return True


def assets(parent_fd: int, folder: str, record: dict[str, Any], value: dict[str, Any]) -> bool:
    try:
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return False
    try:
        info = os.fstat(fd)
        if [info.st_dev, info.st_ino] != record["assets_key"]:
            raise IntegrityError("transfer attachment directory changed")
        wanted = {a["target_name"]: a for a in value["transfer"]["attachments"]}
        with os.scandir(fd) as entries:
            actual = set()
            for entry in entries:
                if entry.name not in wanted:
                    raise IntegrityError("transfer attachment directory has an unexpected entry")
                actual.add(entry.name)
        if actual != set(wanted):
            raise IntegrityError("transfer attachments missing")
        for name, item in wanted.items():
            if not matches(fd, name, record["files"]["assets/" + name], item["sha256"]):
                raise IntegrityError("transfer attachment changed")
        return True
    finally:
        os.close(fd)


def operate(
    execution: str,
    value: dict[str, Any],
    record: dict[str, Any],
    source_fd: int,
    target_fd: int,
    *,
    write: bool,
    guard: Callable[[], None],
) -> bool:
    plan = value["plan"]
    stage, backup, folder = names(execution, value)
    fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=target_fd)
    try:
        info = os.fstat(fd)
        if [info.st_dev, info.st_ino] != record["dir_key"]:
            raise IntegrityError("transfer staging directory changed")
        if value["transfer"]["attachments"]:
            src_assets = assets(fd, "assets", record, value)
            dst_assets = assets(target_fd, folder, record, value)
            if src_assets == dst_assets:
                raise IntegrityError("transfer attachment locations conflict")
            if not dst_assets:
                if not write:
                    return False
                guard()
                file_exchange.rename(fd, "assets", target_fd, folder, exchange=False)
        key, sha = record["files"]["candidate.md"], value["transfer"]["candidate_sha256"]
        candidate = matches(fd, "candidate.md", key, sha)
        target = matches(target_fd, plan["target_path"].rsplit("/", 1)[-1], key, sha)
        if candidate == target:
            raise IntegrityError("transfer note locations conflict or deletion")
        if not target:
            if not write:
                return False
            guard()
            file_exchange.rename(
                fd,
                "candidate.md",
                target_fd,
                plan["target_path"].rsplit("/", 1)[-1],
                exchange=False,
            )
        source = matches(
            source_fd,
            plan["source_path"].rsplit("/", 1)[-1],
            plan["source_file_key"],
            plan["source_sha256"],
        )
        retained = matches(source_fd, backup, plan["source_file_key"], plan["source_sha256"])
        if source == retained:
            raise IntegrityError("transfer source locations conflict or deletion")
        if not retained:
            if not write:
                return False
            guard()
            file_exchange.rename(
                source_fd, plan["source_path"].rsplit("/", 1)[-1], source_fd, backup, exchange=False
            )
        return True
    finally:
        os.close(fd)
