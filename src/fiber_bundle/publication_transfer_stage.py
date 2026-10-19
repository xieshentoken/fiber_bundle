"""Owned whole-bundle copy publication and retained source-note move."""

import hashlib
import json
import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import database, file_exchange, note_transfer_stage, publication_files
from . import note_migration_store as journal
from . import note_transfer_links as links
from . import publication_transfer_intent as intent
from .config import Config, IntegrityError, contained
from .inputs import canonical


def name(execution: str) -> str:
    return ".fiber-publication-transfer-" + execution


def save(config: Config, execution: str, value: dict[str, Any], parent: Path) -> dict[str, Any]:
    files = intent.files(config, execution, value)
    publication_files.stage(parent, name(execution), files)
    root = contained(parent, name(execution))
    info = root.stat()
    record = {
        "schema": 1,
        "dir_key": [info.st_dev, info.st_ino],
        "files": {n: links.read(root, n)[1] for n in sorted(files)},
    }
    raw = canonical(record)
    with journal.connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            db.execute(
                "CREATE TABLE IF NOT EXISTS publication_stages(execution_id TEXT PRIMARY KEY,"
                "record_json TEXT NOT NULL,record_sha256 TEXT NOT NULL) STRICT"
            )
            db.execute(
                "INSERT INTO publication_stages VALUES(?,?,?)",
                (execution, raw, hashlib.sha256(raw.encode()).hexdigest()),
            )
    return record


def load(config: Config, execution: str, value: dict[str, Any]) -> dict[str, Any] | None:
    with journal.connection(config) as db:
        if (
            db is None
            or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='publication_stages'"
            ).fetchone()
        ):
            return None
        row = db.execute(
            "SELECT * FROM publication_stages WHERE execution_id=?", (execution,)
        ).fetchone()
    if row is None:
        return None
    raw = row["record_json"]
    if (
        len(raw.encode()) > 65536
        or hashlib.sha256(raw.encode()).hexdigest() != row["record_sha256"]
    ):
        raise IntegrityError("publication staging identity changed")
    record = json.loads(raw)
    names = {f["target_name"] for f in value["plan"]["bundle_files"]}
    if (
        not isinstance(record, dict)
        or set(record) != {"schema", "dir_key", "files"}
        or type(record["schema"]) is not int
        or record["schema"] != 1
        or canonical(record) != raw
        or not isinstance(record["files"], dict)
        or set(record["files"]) != names
    ):
        raise IntegrityError("invalid publication staging scope")
    for key, length in [(record["dir_key"], 2)] + [(k, 3) for k in record["files"].values()]:
        if (
            not isinstance(key, list)
            or len(key) != length
            or any(type(v) is not int or v < 0 for v in key[:2])
        ):
            raise IntegrityError("invalid publication staging physical key")
        if length == 3:
            if not isinstance(key[2], str):
                raise IntegrityError("invalid publication staging birth type")
            birth = float.fromhex(key[2])
            if not math.isfinite(birth) or birth.hex() != key[2]:
                raise IntegrityError("invalid publication staging birth")
    return record


def matches(parent: int, folder: str, record: dict[str, Any], value: dict[str, Any]) -> bool:
    try:
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except FileNotFoundError:
        return False
    try:
        info = os.fstat(fd)
        if [info.st_dev, info.st_ino] != record["dir_key"]:
            raise IntegrityError("publication transfer directory replaced")
        expected = {f["target_name"]: f for f in value["plan"]["bundle_files"]}
        directories = {n.split("/", 1)[0] for n in expected if "/" in n}
        with os.scandir(fd) as entries:
            actual = set()
            for entry in entries:
                if entry.name not in expected and entry.name not in directories:
                    raise IntegrityError("publication transfer contains unexpected entries")
                actual.add(entry.name)
        if actual != {n for n in expected if "/" not in n} | directories:
            raise IntegrityError("publication transfer files missing")
        for directory in directories:
            sub = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                wanted = {n.split("/", 1)[1] for n in expected if n.startswith(directory + "/")}
                with os.scandir(sub) as entries:
                    actual = set()
                    for entry in entries:
                        if entry.name not in wanted:
                            raise IntegrityError("publication transfer attachment scope changed")
                        actual.add(entry.name)
                if actual != wanted:
                    raise IntegrityError("publication transfer attachment missing")
                for basename in wanted:
                    path = directory + "/" + basename
                    if not note_transfer_stage.matches(
                        sub, basename, record["files"][path], expected[path]["sha256"]
                    ):
                        raise IntegrityError("publication transfer attachment changed")
            finally:
                os.close(sub)
        for path, item in expected.items():
            if "/" not in path and not note_transfer_stage.matches(
                fd, path, record["files"][path], item["sha256"]
            ):
                raise IntegrityError("publication transfer note or marker changed")
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
    bundle = plan["target_path"].rsplit("/", 1)[0].rsplit("/", 1)[-1]
    staged, published = (
        matches(target_fd, name(execution), record, value),
        matches(target_fd, bundle, record, value),
    )
    if staged == published:
        raise IntegrityError("publication transfer bundle locations conflict or deletion")
    if not published:
        if not write:
            return False
        guard()
        file_exchange.rename(target_fd, name(execution), target_fd, bundle, exchange=False)
    basename = plan["source_path"].rsplit("/", 1)[-1]
    hidden = ".fiber-moved-" + execution + ".md"
    source = note_transfer_stage.matches(
        source_fd, basename, plan["source_file_key"], plan["source_sha256"]
    )
    retained = note_transfer_stage.matches(
        source_fd, hidden, plan["source_file_key"], plan["source_sha256"]
    )
    if source == retained:
        raise IntegrityError("publication transfer source note conflict or deletion")
    if not retained:
        if not write:
            return False
        guard()
        file_exchange.rename(source_fd, basename, source_fd, hidden, exchange=False)
    return True
