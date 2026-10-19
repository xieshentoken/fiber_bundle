"""Reviewed historical transfer artifacts and strict dependency metadata."""

import hashlib
import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import (
    note_changes,
    note_identity_store,
    publication_files,
)
from . import (
    note_migration_plans as plans,
)
from . import (
    note_transfer_links as links,
)
from . import (
    note_transfer_mocs as mocs,
)
from .article_capture import read_bounded
from .config import Config, IntegrityError, contained, make_directory
from .inputs import text

FIELDS = {
    "candidate_sha256",
    "attachments",
    "attachment_folder",
    "unresolved_note_links",
    "moc_changes",
}


def local_path(value: Any) -> None:
    text(value, limit=1024)
    if (
        not isinstance(value, str)
        or "\\" in value
        or "\x00" in value
        or len(value.split("/")) > 33
        or any(p in {"", ".", ".."} or p.startswith(".") for p in value.split("/"))
    ):
        raise IntegrityError("invalid migration local dependency path")


def validate_inbound(items: Any) -> None:
    if not isinstance(items, list) or len(items) > 10000:
        raise IntegrityError("invalid incoming note-link scope")
    for item in items:
        if not isinstance(item, dict) or set(item) != {"note_path", "line", "target"}:
            raise IntegrityError("invalid incoming note link")
        local_path(item["note_path"])
        text(item["target"], limit=4096)
        if type(item["line"]) is not int or item["line"] < 1:
            raise IntegrityError("invalid incoming note-link location")


def validate(value: dict[str, Any]) -> None:
    transfer, plan = value["transfer"], value["plan"]
    if not isinstance(transfer, dict) or set(transfer) not in (
        FIELDS,
        FIELDS | {"incoming_note_links"},
    ):
        raise IntegrityError("invalid reviewed migration dependency fields")
    validate_inbound(transfer.get("incoming_note_links", []))
    namespace = plans.digest({"plan_id": value["plan_id"], "request_id": value["request_id"]})[:16]
    folder = "fiber-attachments-" + plan["note_identity"].split(":", 1)[1] + "-" + namespace
    if transfer["attachment_folder"] != folder:
        raise IntegrityError("invalid migration attachment folder binding")
    if any(f in plan["review_flags"] for f in {"moc_document_needs_review"}):
        raise IntegrityError("MOC documents require their own migration workflow")
    if not isinstance(transfer["candidate_sha256"], str) or not note_changes.DIGEST.fullmatch(
        transfer["candidate_sha256"]
    ):
        raise IntegrityError("invalid transfer candidate digest")
    attachments = transfer["attachments"]
    if not isinstance(attachments, list) or len(attachments) > links.MAX_ATTACHMENTS:
        raise IntegrityError("invalid transfer attachment scope")
    paths = []
    for item in attachments:
        if not isinstance(item, dict) or set(item) != {
            "source_path",
            "file_key",
            "sha256",
            "bytes",
            "target_name",
        }:
            raise IntegrityError("invalid transfer attachment metadata")
        path = item["source_path"]
        local_path(path)
        if path.lower().endswith(".md") or any(p.startswith(".") for p in path.split("/")):
            raise IntegrityError("migration attachment cannot be a hidden file or note")
        key = item["file_key"]
        if (
            not isinstance(key, list)
            or len(key) != 3
            or any(type(v) is not int or v < 0 for v in key[:2])
            or not isinstance(key[2], str)
        ):
            raise IntegrityError("invalid attachment physical identity")
        birth = float.fromhex(key[2])
        if not math.isfinite(birth) or birth.hex() != key[2]:
            raise IntegrityError("invalid attachment birth")
        if type(item["bytes"]) is not int or not 0 < item["bytes"] <= links.MAX_ATTACHMENT_BYTES:
            raise IntegrityError("invalid attachment size")
        if not isinstance(item["sha256"], str) or not note_changes.DIGEST.fullmatch(item["sha256"]):
            raise IntegrityError("invalid attachment digest")
        name = item["target_name"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[0-9a-f]{64}\.[a-z0-9]{1,16}", name)
            or not name.startswith(item["sha256"])
        ):
            raise IntegrityError("invalid attachment destination name")
        paths.append(path)
    if paths != sorted(set(paths)):
        raise IntegrityError("transfer attachment scope must be unique and ordered")
    unresolved = transfer["unresolved_note_links"]
    if (
        not isinstance(unresolved, list)
        or len(unresolved) > 10000
        or unresolved != sorted(set(unresolved))
    ):
        raise IntegrityError("invalid unresolved migration note links")
    for path in unresolved:
        local_path(path)
    mocs.validate(transfer["moc_changes"], plan)
    request = value["request_id"]
    if not isinstance(request, str) or len(request) > 64:
        raise IntegrityError("invalid migration request ID")
    if not note_changes.DIGEST.fullmatch(request):
        note_changes.identifier(request)


def create(
    config: Config,
    plan_id: str,
    plan: dict[str, Any],
    root: Path,
    target_moc: str | None,
    revision: int | None,
    request: str | None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    source = contained(root, plan["source_path"])
    data = read_bounded(source.parent, source.name, 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != plan["source_sha256"]:
        raise IntegrityError("migration source body changed")
    namespace = plans.digest({"plan_id": plan_id, "request_id": request or plan_id})[:16]
    candidate, copies, attachments, unresolved = links.prepare(
        root,
        plan["source_path"],
        plan["target_path"],
        plan["note_identity"],
        data,
        namespace=namespace,
    )
    if len(candidate) > 1024 * 1024:
        raise IntegrityError("migration candidate exceeds note limit")
    before = next(
        (
            r
            for r in note_identity_store.records(config, plan["source_vault_id"])
            if r.identity == plan["note_identity"]
        ),
        None,
    )
    if before is None or plans.digest(asdict(before)) != plan["registration_sha256"]:
        raise IntegrityError("migration source registration changed")
    transfer = {
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "attachments": attachments,
        "attachment_folder": "fiber-attachments-"
        + plan["note_identity"].split(":", 1)[1]
        + "-"
        + namespace,
        "unresolved_note_links": unresolved,
        "moc_changes": mocs.freeze(config, plan, target_moc, revision),
    }
    value = {
        "schema": 3,
        "action": "transfer_reviewed_note",
        "plan_id": plan_id,
        "plan": plan,
        "registration": asdict(before),
        "request_id": request or plan_id,
        "transfer": transfer,
    }
    validate(value)
    files = {
        "source.md": data,
        "candidate.md": candidate,
        **{"assets/" + name: payload for name, payload in copies.items()},
    }
    return value, files


def recheck(config: Config, execution: str, value: dict[str, Any], root: Path) -> None:
    plan = value["plan"]
    original = contained(root, plan["source_path"])
    data = read_bounded(original.parent, original.name, 1024 * 1024)
    namespace = plans.digest({"plan_id": value["plan_id"], "request_id": value["request_id"]})[:16]
    candidate, copies, attachments, unresolved = links.prepare(
        root,
        plan["source_path"],
        plan["target_path"],
        plan["note_identity"],
        data,
        namespace=namespace,
    )
    transfer = value["transfer"]
    if (
        hashlib.sha256(data).hexdigest() != plan["source_sha256"]
        or hashlib.sha256(candidate).hexdigest() != transfer["candidate_sha256"]
        or attachments != transfer["attachments"]
        or unresolved != transfer["unresolved_note_links"]
    ):
        raise IntegrityError("reviewed migration body or attachment derivation changed")
    private = files(config, execution, value)
    if private != {
        "source.md": data,
        "candidate.md": candidate,
        **{"assets/" + name: payload for name, payload in copies.items()},
    }:
        raise IntegrityError("reviewed migration candidate differs from current source")


def directory(config: Config, execution: str) -> Path:
    if not note_changes.DIGEST.fullmatch(execution):
        raise IntegrityError("invalid transfer execution digest")
    return contained(config.state_root, "note-transfer-files/" + execution)


def save_files(config: Config, execution: str, files: dict[str, bytes]) -> None:
    path = directory(config, execution)
    make_directory(path.parent)
    if path.exists():
        publication_files.verify(path.parent, path.name, files)
    else:
        publication_files.stage(path.parent, path.name, files)


def files(config: Config, execution: str, value: dict[str, Any]) -> dict[str, bytes]:
    root = directory(config, execution)
    result = {name: read_bounded(root, name, 1024 * 1024) for name in ("source.md", "candidate.md")}
    expected = {
        "source.md": value["plan"]["source_sha256"],
        "candidate.md": value["transfer"]["candidate_sha256"],
    }
    total = 0
    for item in value["transfer"]["attachments"]:
        name = "assets/" + item["target_name"]
        if name in result:
            continue
        payload = read_bounded(contained(root, "assets"), item["target_name"], item["bytes"])
        result[name] = payload
        expected[name] = item["sha256"]
        total += len(payload)
        if total > links.MAX_TOTAL_BYTES:
            raise IntegrityError("transfer attachment byte limit exceeded")
    if any(hashlib.sha256(result[name]).hexdigest() != sha for name, sha in expected.items()):
        raise IntegrityError("transfer private artifacts changed")
    publication_files.verify(root.parent, root.name, result)
    return result


def check_attachments(root: Path, value: dict[str, Any]) -> None:
    for item in value["transfer"]["attachments"]:
        data, key = links.read(root, item["source_path"])
        if (
            key != item["file_key"]
            or len(data) != item["bytes"]
            or hashlib.sha256(data).hexdigest() != item["sha256"]
        ):
            raise IntegrityError("migration source attachment changed")
