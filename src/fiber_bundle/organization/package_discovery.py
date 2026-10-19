"""Scope-aware Book admission. File inspection stays outside Fiber's writer lease."""

import os
import stat
import time
from typing import Any

from .. import auth, database, file_exchange, vault_directories, vault_save_policy
from ..config import IntegrityError, StorageError, contained
from ..expert_package_boundary import detected
from ..inputs import canonical
from ..objects import digest
from . import packages, store

INTERVAL = 60


def key(client: str, vault: str) -> str:
    return vault + ":" + client


def status(config: Any, client: str, vault: str) -> Any:
    return store.get(config, "package_scans", key(client, vault)) or {
        "state": "idle",
        "items": [],
        "client_id": client,
        "vault_id": vault,
    }


def request(config: Any, db: Any, client: str, vault: str) -> Any:
    packages.context(config, db, client, vault, write=True)
    value = status(config, client, vault)
    if value["state"] != "queued":
        value = {**value, "state": "queued", "requested_at": time.time()}
        store.put(config, "package_scans", key(client, vault), vault, value)
    return value


def candidates(root: Any, allowed: Any, book: str) -> list[str]:
    """Stop at every package boundary, including malformed/incomplete copied Skills."""
    found = []
    count = 0

    def walk(relative: str, depth: int) -> None:
        nonlocal count
        if not allowed.reachable(relative):
            return
        if depth > 32:
            raise IntegrityError("expert_package_scan_depth_limit")
        if relative and detected(root, relative, allowed):
            found.append(relative)
            return
        with vault_directories.opened_parent(root, allowed, relative) as (_, fd):
            with os.scandir(fd) as entries:
                children = []
                for entry in entries:
                    child = relative + "/" + entry.name if relative else entry.name
                    if entry.name.startswith(".") or not allowed.reachable(child):
                        continue
                    count += 1
                    if count > 10000:
                        raise IntegrityError("expert_package_scan_entry_limit")
                    if entry.is_dir(follow_symlinks=False):
                        children.append(child)
            for child in sorted(children):
                walk(child, depth + 1)

    try:
        walk(book, 0)
    except FileNotFoundError:
        if (root / book).exists():
            raise  # A member changed during traversal, not an absent Book folder.
    return found


def fingerprint(root: Any, allowed: Any, relative: str, names: Any) -> str:
    evidence = []
    for name in sorted(names):
        path = relative + "/" + name
        if not allowed.allows(path):
            raise IntegrityError("expert_package_scope_denied")
        contained(root, path)  # Reject traversal/symlinks before any member metadata probe.
        fd = file_exchange.parent_fd(root, path)
        try:
            info = os.stat(name.rsplit("/", 1)[-1], dir_fd=fd, follow_symlinks=False)
        finally:
            os.close(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise IntegrityError("expert_package_member_identity")
        evidence.append(
            [name, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        )
    return digest(canonical([allowed.wire(), evidence]).encode())


def scan(config: Any, client: str, vault: str) -> Any:
    """One pass; unchanged packages reuse a full prior integrity inspection, never a model."""
    from ..agent_connections.visibility import scope

    prior = status(config, client, vault)
    old = {item["path"]: item for item in prior["items"]}
    rows = []
    with database.connect(config.state_root) as db:
        root, allowed = packages.context(config, db, client, vault, write=True)
        if scope(config, db, client, [vault])[1] != "all":
            raise auth.Denied(404)
        book = vault_save_policy.get(config, db, vault)["settings"].get("book_dir", "Book")
        for path in candidates(root, allowed, book):
            cached = old.get(path, {})
            try:
                active = store.get(config, "packages", cached.get("id", ""))
                if active:
                    stamp = fingerprint(root, allowed, path, active["files"])
                    if (
                        cached.get("fingerprint") == stamp
                        and cached.get("version") == active["version"]
                    ):
                        rows.append(cached)
                        continue
                # Freeze member metadata before inspection; recheck before ledger commit.
                import json

                manifest = json.loads(packages.scoped_read(root, allowed, path + "/manifest.json"))
                names = set(manifest["integrity"]["files"]) | {"manifest.json"}
                before = fingerprint(root, allowed, path, names)
                value = packages.inspect(config, db, client, vault, path)
                with database.locked(config):
                    _, now_allowed = packages.context(config, db, client, vault, write=True)
                    if scope(config, db, client, [vault])[1] != "all":
                        raise auth.Denied(404)
                    if fingerprint(root, now_allowed, path, names) != before:
                        raise IntegrityError("expert_package_changed_during_scan")
                    # An explicit path/policy change during scanning invalidates admission.
                    current_book = vault_save_policy.get(config, db, vault)["settings"].get(
                        "book_dir", "Book"
                    )
                    if current_book != book:
                        raise IntegrityError("expert_package_book_changed")
                    registered = packages.commit(config, db, client, vault, value)
                rows.append(
                    {
                        **registered,
                        "path": path,
                        "registered_path": registered["path"],
                        "state": "registered",
                        "fingerprint": before,
                    }
                )
            except (
                auth.Denied,
                StorageError,
                OSError,
                ValueError,
                KeyError,
                TypeError,
                RecursionError,
            ) as error:
                rows.append({"path": path, "state": "failed", "error": str(error)[:180]})
    result = {
        "client_id": client,
        "vault_id": vault,
        "state": "ready",
        "items": rows,
        "book_dir": book,
        "checked_at": time.time(),
        "model_called": False,
    }
    with database.locked(config), database.connect(config.state_root) as db:
        packages.context(config, db, client, vault, write=True)
        store.put(config, "package_scans", key(client, vault), vault, result)
    return result


def tick(config: Any) -> None:
    # Configured classification identities and explicit GUI requests opt a vault into discovery.
    sources = {
        key(p["client_id"], p["vault_id"]): p
        for p in store.rows(config, "policies")
        if p.get("client_id")
    }
    sources.update(
        {key(p["client_id"], p["vault_id"]): p for p in store.rows(config, "package_scans")}
    )
    now = time.time()
    for identity, source in sources.items():
        client, vault = source["client_id"], source["vault_id"]
        prior = status(config, client, vault)
        if prior["state"] != "queued" and now - prior.get("checked_at", 0) < INTERVAL:
            continue
        try:
            scan(config, client, vault)
        except (auth.Denied, StorageError, OSError, ValueError) as error:
            # Persist a bounded diagnostic, not a perpetual spinner. Current grants gate UI reads.
            with database.locked(config):
                store.put(
                    config,
                    "package_scans",
                    identity,
                    vault,
                    {**prior, "state": "failed", "checked_at": now, "error": str(error)[:180]},
                )
        return  # One vault per tick; no long all-vault writer transaction.


def visible(config: Any, db: Any, client: str, vault: str) -> Any:
    from ..agent_connections.visibility import scope

    _, quality = scope(config, db, client, [vault])
    _, allowed = packages.context(config, db, client, vault)
    value = status(config, client, vault)
    return {k: v for k, v in value.items() if k != "items"} | {
        "items": [
            {k: v for k, v in item.items() if k not in {"fingerprint", "reused"}}
            for item in value["items"]
            if quality == "all" and allowed.allows(item["path"] + "/manifest.json")
        ]
    }


def run(config: Any, stopped: Any) -> None:
    """Independent of model/extraction waits; never starts a second classification worker."""
    import logging

    while not stopped.wait(2):
        try:
            tick(config)
        except Exception:
            logging.getLogger(__name__).warning("Book discovery requires reconciliation")
