"""Frozen source/Book/MOC evidence for explicit external Draft imports."""

import re
import shutil
import time
from pathlib import Path
from typing import Any

from .. import auth, vault_directories, vault_save_policy
from ..config import IntegrityError, InvalidInput
from ..inputs import canonical
from ..objects import digest
from . import catalog, packages, store
from . import draft_import_files as files


def target(config: Any, db: Any, client: str, vault: str) -> dict[str, Any]:
    root, allowed = packages.context(config, db, client, vault, write=True)
    policy = vault_save_policy.get(config, db, vault)
    if not policy["publication_ready"] or policy["settings"]["policy_version"] != 2:
        raise InvalidInput("draft_book_rules_not_ready")
    if policy["read_only"] or policy["pending_operation"]:
        raise InvalidInput("draft_target_not_writable")
    book = policy["settings"]["book_dir"]
    if not book or not allowed.allows(book):
        raise InvalidInput("draft_book_scope_denied")
    directory = vault_directories.parent_path(root, allowed, book)
    if not directory.is_dir():
        raise InvalidInput("draft_book_directory_missing")
    grant = auth.grant(db, client, vault, "add")
    return {
        "root": str(root),
        "root_key": files.key(root),
        "book_dir": book,
        "book_key": files.key(directory),
        "scope": allowed.wire(),
        "policy_revision": policy["revision"],
        "grant": dict(grant),
        "permission_epoch": list(
            db.execute(
                "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
                (client, vault),
            ).fetchone()
            or [0]
        ),
    }


def name(value: str) -> str:
    if (
        not value
        or len(value) > 160
        or value.startswith(".")
        or re.search(r"[/\\\x00-\x1f:]", value)
        or value != value.strip()
    ):
        raise InvalidInput("draft_target_name_invalid")
    return value


def prepare(config: Any, scope: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Heavy source checks run without Fiber's global writer lease."""
    folder = name(scope.get("folder") or Path(scope["source_path"]).name)
    relative = evidence["book_dir"] + "/" + folder
    observed = files.inspect(
        scope["source_path"], scope["vault_id"], relative, evidence["root_key"]
    )
    return {**scope, **observed, "target_evidence": evidence, "relative_path": relative}


def finish(config: Any, db: Any, task: dict[str, Any]) -> dict[str, Any]:
    client, vault = task["client_id"], task["vault_id"]
    evidence = target(config, db, client, vault)
    if evidence != task["target_evidence"]:
        raise IntegrityError("draft_target_changed")
    root, allowed = packages.context(config, db, client, vault, write=True)
    package = task["package"]
    previous = store.get(config, "packages", package["id"])
    if previous and previous["version"] != package["version"]:
        raise InvalidInput("draft_version_upgrade_requires_review")
    if previous:
        # Revalidate rather than reuse a stale record or duplicate its files.
        current = packages.inspect(config, db, client, vault, previous["path"])
        if current["version"] != package["version"]:
            raise IntegrityError("draft_registered_package_changed")
        task["relative_path"] = previous["path"]
        task["package"] = current
        task["reuse"] = True
    elif (root / task["relative_path"]).exists():
        candidate = root / task["relative_path"]
        if candidate == Path(task["source_path"]):
            task["reuse"] = True
        else:
            raise InvalidInput("draft_destination_exists")
    else:
        task["reuse"] = False
        vault_save_policy.alias(
            root, evidence["book_dir"], Path(task["relative_path"]).name, allowed
        )
    free = shutil.disk_usage(root / evidence["book_dir"]).free
    required = (
        0 if task["reuse"] else task["total_bytes"] + max(16 * 1024**2, task["total_bytes"] // 20)
    )
    if free < required:
        raise InvalidInput("draft_insufficient_space")
    _, mocs, _ = catalog.snapshot(config, db, client, vault)
    task["mocs"] = [{"id": m["id"], "title": m["title"], "sha256": m["sha256"]} for m in mocs]
    task["free_bytes"], task["required_bytes"] = free, required
    task["preview_expires"] = time.time() + 3600
    task["preview_sha256"] = digest(
        canonical(
            {
                k: task[k]
                for k in (
                    "id",
                    "source_path",
                    "source_digest",
                    "package",
                    "target_evidence",
                    "relative_path",
                    "mocs",
                    "preview_expires",
                )
            }
        ).encode()
    )
    task.update(state="preview", phase="preview", error=None)
    return task


def recheck_target(config: Any, db: Any, task: dict[str, Any]) -> tuple[Any, Any]:
    if target(config, db, task["client_id"], task["vault_id"]) != task["target_evidence"]:
        raise IntegrityError("draft_target_changed")
    root, allowed = packages.context(config, db, task["client_id"], task["vault_id"], write=True)
    return root, allowed
