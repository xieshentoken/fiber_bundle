"""Explicit sealed Inbox plans and scope-filtered processing history."""

import base64
import time
from dataclasses import asdict
from itertools import zip_longest
from typing import Any
from uuid import uuid4

from .. import auth, note_identity_store, publication_location_selection
from ..config import IntegrityError, InvalidInput, StorageError
from ..inputs import canonical
from ..objects import digest
from . import inbox_review as review
from . import inbox_review_ledger as ledger
from . import store

TABLE = "inbox_review_operations"


def line_changes(before: bytes, after: bytes) -> list[Any]:
    changes: list[Any] = [
        {"line": i + 1, "before": old, "after": new}
        for i, (old, new) in enumerate(
            zip_longest(
                before.decode("utf-8-sig").splitlines(),
                after.decode("utf-8-sig").splitlines(),
                fillvalue="",
            )
        )
        if old != new
    ]
    if any(
        len(change[part].encode()) > review.DISPLAY_BYTES
        for change in changes
        for part in ("before", "after")
    ):
        raise InvalidInput("inbox_review_diff_limit")
    return changes


def public(value: Any) -> Any:
    return {
        "operation_id": value["id"],
        "created_at": value["created_at"],
        "state": value["state"],
        "action": value["action"],
        "path": value["path"],
        "plan_sha256": value["plan_sha256"],
        "changes": value["changes"],
        "impact": {
            "moves_only_this_file": "quarantine" in value["action"],
            "preserves_attachments": True,
            "preserves_history": True,
            "does_not_resume_import": True,
        },
        "can_apply": value["state"] == "prepared",
        "can_restore": bool(value["state"] == "completed" or value.get("restore_only", False))
        and not value.get("restored_by")
        and (not value["action"].startswith("restore_") or bool(value.get("restore_only"))),
        "can_reconcile": value["state"] in {"applying", "reconcile"}
        and not value.get("restore_only"),
        "index_state": "repreview_required",
        "model_called": False,
        "error": value.get("error"),
    }


def save(config: Any, value: Any, state: str) -> None:
    value["state"] = state
    value["updated_at"] = time.time()
    store.put(config, TABLE, value["id"], value["vault_id"], value)


def load(config: Any, db: Any, client: str, vault: str, identity: str) -> Any:
    from .packages import context

    context(config, db, client, vault, write=True)
    value = store.get(config, TABLE, identity)
    if not value or value["client_id"] != client or value["vault_id"] != vault:
        raise auth.Denied(404)
    review.context(config, db, client, vault, value["path"])
    return value


def history(config: Any, db: Any, client: str, vault: str, path: str | None = None) -> Any:
    from .packages import context

    context(config, db, client, vault, write=True)
    result = []
    rows = ledger.history(config, vault, client, path)
    for value in rows[:500]:
        try:
            review.context(config, db, client, vault, value["path"])
        except (auth.Denied, StorageError, OSError):
            continue
        result.append(public(value))
        if len(result) == 100:
            break
    return {"operations": result, "truncated": len(rows) > 500 or len(result) == 100}


def _new(
    config: Any,
    db: Any,
    client: str,
    vault: str,
    path: str,
    job_id: str,
    action: str,
    fixed: Any,
    before: bytes,
    evidence: Any,
    after: bytes,
    changes: list[Any],
    **extra: Any,
) -> Any:
    value = {
        "id": str(uuid4()),
        "client_id": client,
        "vault_id": vault,
        "job_id": job_id,
        "path": path,
        "action": action,
        "created_at": time.time(),
        "fixed": fixed,
        "before": base64.b64encode(before).decode(),
        "after": base64.b64encode(after).decode(),
        "evidence": evidence,
        "changes": changes,
        "locations_before": publication_location_selection.affected(db, vault, {path}),
        "registrations_before": [
            asdict(r) for r in note_identity_store.records(config, vault) if r.relative_path == path
        ],
        **extra,
    }
    value["sealed_keys"] = sorted([*value, "sealed_keys"])
    value["plan_sha256"] = seal(value)
    save(config, value, "prepared")
    return public(value)


def seal(value: Any) -> str:
    return digest(canonical({key: value[key] for key in value["sealed_keys"]}).encode())


def preview(
    config: Any,
    db: Any,
    client: str,
    vault: str,
    job_id: str,
    path: str,
    action: str,
    link_ids: list[str],
) -> Any:
    root, allowed, fixed = review.context(config, db, client, vault, path)
    job = review.task(config, db, client, vault, job_id, path)
    raw, evidence = review.read(root, path)
    state = review.classification(raw)
    changes: list[Any] = []
    after = raw
    if action == "quarantine_empty":
        if link_ids or state not in {"empty", "title_only"}:
            raise InvalidInput("inbox_review_not_empty")
    elif action == "unlink_missing":
        links = {link["id"]: link for link in review.repairable(root, allowed, job, path, raw)}
        if not link_ids or len(set(link_ids)) != len(link_ids) or not set(link_ids) <= links.keys():
            raise InvalidInput("inbox_review_link_selection_changed")
        selected = [links[identity] for identity in link_ids]
        text = raw.decode("utf-8-sig")
        for link in sorted(selected, key=lambda link: link["start"], reverse=True):
            text = text[: link["start"]] + link["replacement"] + text[link["stop"] :]
        after = (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + text.encode()
        if len(after) > review.MAX_BYTES:
            raise InvalidInput("inbox_review_file_limit")
        changes = line_changes(raw, after)
        if any(
            row["state"] != "published"
            for row in publication_location_selection.affected(db, vault, {path})
        ):
            raise InvalidInput("inbox_review_publication_not_committed")
    else:
        raise InvalidInput("inbox_review_invalid_action")
    # Do not create a second plan while a file mutation still needs explicit reconciliation.
    if ledger.pending(config, vault, path):
        raise IntegrityError("inbox_review_reconciliation_required")
    return _new(
        config,
        db,
        client,
        vault,
        path,
        job_id,
        action,
        fixed,
        raw,
        evidence,
        after,
        changes,
        link_ids=link_ids,
    )


def restore_preview(config: Any, db: Any, client: str, vault: str, identity: str) -> Any:
    original = load(config, db, client, vault, identity)
    if (
        original["state"] != "completed"
        and not original.get("restore_only")
        or original["action"].startswith("restore_")
        and not original.get("restore_only")
    ):
        raise InvalidInput("inbox_review_restore_not_ready")
    if original.get("restored_by"):
        return public(load(config, db, client, vault, original["restored_by"]))
    root, _, fixed = review.context(config, db, client, vault, original["path"])
    if original["fixed"] != fixed:
        raise IntegrityError("inbox_review_scope_or_directory_changed")
    from .inbox_review_execution import check_restorable

    evidence = check_restorable(root, original)
    quarantine = original["action"] == "quarantine_empty"
    raw = (
        review.read(root, original["stage_name"] + "/quarantine.md")[0]
        if quarantine
        else base64.b64decode(original.get("restore_source", original["before"]), validate=True)
    )
    before = raw if quarantine else base64.b64decode(original["after"], validate=True)
    return _new(
        config,
        db,
        client,
        vault,
        original["path"],
        original["job_id"],
        "restore_quarantine" if quarantine else "restore_links",
        fixed,
        before,
        evidence,
        raw,
        line_changes(b"" if quarantine else before, raw)
        if (not quarantine or original.get("restore_only"))
        else [],
        restores=identity,
    )
