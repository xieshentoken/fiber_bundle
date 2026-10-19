"""Immutable local MOC write plans, exclusive claims and outcome records."""

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import file_exchange, moc_dispatch_source, moc_file_identity, note_changes, note_index
from .config import Config, IntegrityError, contained, make_directory, sync_directory
from .inputs import canonical

MAX_PLANS = 1000
FIELDS = {
    "schema",
    "operation",
    "vault_id",
    "moc_id",
    "revision",
    "path",
    "before_sha256",
    "after_sha256",
    "file_key",
    "vault_key",
    "parent_key",
    "inventory_sha256",
    "managed_sha256",
    "base_id",
    "manual_links_preserved",
}


@dataclass(frozen=True)
class Plan:
    plan_id: str
    path: Path
    value: dict[str, Any]
    status: str


def directory(config: Config, vault_id: str, moc_id: str) -> Path:
    note_changes.identifier(vault_id)
    note_changes.identifier(moc_id)
    return contained(config.state_root, f"moc-files/{vault_id}/{moc_id}")


def read(path: Path) -> bytes:
    fd = file_exchange.parent_fd(path.parent, path.name)
    try:
        return file_exchange.read(fd, path.name)[0]
    finally:
        os.close(fd)


def write(path: Path, data: bytes) -> None:
    contained(path.parent, path.name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def validate(value: Any, vault_id: str, moc_id: str) -> None:
    if not isinstance(value, dict):
        raise IntegrityError("invalid MOC write plan fields")
    schema = value.get("schema")
    fields = FIELDS | {"identity", "after_file_key"} if schema in {2, 3, 4} else FIELDS
    if schema == 3:
        fields |= {"dispatch_source"}
    if schema == 4:
        fields |= {"graph_writeback_id"}
    if type(schema) is not int or schema not in {1, 2, 3, 4} or set(value) != fields:
        raise IntegrityError("invalid MOC write plan fields")
    if (value["vault_id"], value["moc_id"]) != (vault_id, moc_id):
        raise IntegrityError("MOC write plan scope mismatch")
    note_changes.identifier(value["operation"])
    for name in ["after_sha256", "inventory_sha256", "managed_sha256"]:
        if not isinstance(value[name], str) or not note_changes.DIGEST.fullmatch(value[name]):
            raise IntegrityError("invalid MOC write plan digest")
    if value["before_sha256"] != "absent" and not (
        isinstance(value["before_sha256"], str)
        and note_changes.DIGEST.fullmatch(value["before_sha256"])
    ):
        raise IntegrityError("invalid MOC expected digest")
    if value["base_id"] is not None and not (
        isinstance(value["base_id"], str) and note_changes.DIGEST.fullmatch(value["base_id"])
    ):
        raise IntegrityError("invalid MOC base plan")
    for key in ["vault_key", "parent_key", "file_key"]:
        parts = value[key]
        if key == "file_key" and parts is None and value["before_sha256"] == "absent":
            continue
        if (
            not isinstance(parts, list)
            or len(parts) != 2
            or any(type(v) is not int or v < 0 for v in parts)
        ):
            raise IntegrityError("invalid MOC filesystem binding")
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise IntegrityError("invalid MOC revision")
    if (
        type(value["manual_links_preserved"]) is not int
        or not 0 <= value["manual_links_preserved"] <= 1000
    ):
        raise IntegrityError("invalid MOC manual link count")
    path = value["path"]
    note_changes.validate(
        [{"identity": "path:" + path, "note_path": path, "sha256": value["after_sha256"]}]
    )
    if not path.lower().endswith(".md"):
        raise IntegrityError("MOC write target must be Markdown")
    moc_file_identity.transition(value)
    if schema == 4 and not note_changes.DIGEST.fullmatch(value["graph_writeback_id"]):
        raise IntegrityError("invalid graph MOC binding")
    if schema == 3:
        moc_dispatch_source.validate_plan(value)
        if moc_file_identity.file_key(value["after_file_key"])[0] != value["vault_key"][0]:
            raise IntegrityError("MOC dispatch candidate device changed")


def plans(config: Config, vault_id: str, moc_id: str) -> list[Plan]:
    root = directory(config, vault_id, moc_id)
    if not root.exists():
        return []
    result: list[Plan] = []
    with os.scandir(root) as entries:
        for entry in entries:
            if len(result) >= MAX_PLANS or not note_changes.DIGEST.fullmatch(entry.name):
                raise IntegrityError("MOC plan limit or unexpected entry")
            path = contained(root, entry.name)
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise IntegrityError("MOC plan is not a directory")
            raw = read(contained(path, "plan.json"))
            if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != entry.name:
                raise IntegrityError("MOC plan digest mismatch")
            try:
                value = json.loads(raw)
                validate(value, vault_id, moc_id)
                if canonical(value).encode() != raw:
                    raise IntegrityError("MOC plan is not canonical")
            except (ValueError, TypeError, RecursionError) as exc:
                raise IntegrityError("invalid MOC plan") from exc
            claim, outcome = contained(path, "claim"), contained(path, "outcome")
            status = "planned"
            if claim.exists():
                if read(claim) != b"claimed\n":
                    raise IntegrityError("invalid MOC claim")
                status = "pending"
            if outcome.exists():
                if status != "pending":
                    raise IntegrityError("MOC outcome has no claim")
                saved = read(outcome)
                if saved not in {b"applied\n", b"not-applied\n"}:
                    raise IntegrityError("invalid MOC outcome")
                status = saved.decode().strip()
            reverted = contained(path, "reverted")
            if reverted.exists():
                if read(reverted) != b"reverted\n":
                    raise IntegrityError("invalid MOC compensation marker")
                status = "reverted"
            result.append(Plan(entry.name, path, value, status))
    return result


def latest(items: list[Plan]) -> Plan | None:
    applied = [p for p in items if p.status == "applied"]
    current: Plan | None = None
    for _ in range(len(applied)):
        base = current.plan_id if current else None
        children = [p for p in applied if p.value["base_id"] == base]
        if len(children) != 1:
            raise IntegrityError("MOC applied history is not a single chain")
        current = children[0]
    return current


def create(config: Config, value: dict[str, Any], candidate: bytes) -> Plan:
    vid, mid = value["vault_id"], value["moc_id"]
    if len(plans(config, vid, mid)) >= MAX_PLANS:
        raise IntegrityError("MOC write plan limit exceeded")
    staged = None
    if value["schema"] in {2, 3, 4}:
        # Bind the exact candidate inode/birth before publishing its immutable plan.
        # A crash leaves private staging evidence, never an actionable plan.
        staged = Path(tempfile.mkdtemp(prefix=".moc-candidate-", dir=config.state_root))
        write(staged / "candidate.md", candidate)
        note = note_index.read_note(staged, "candidate.md", note_index.Limits().note_bytes)
        if note.file_key is None:
            raise IntegrityError("MOC candidate file birth unavailable")
        value["after_file_key"] = list(note.file_key)
    validate(value, vid, mid)
    raw = canonical(value).encode()
    identity = hashlib.sha256(raw).hexdigest()
    root = directory(config, vid, mid)
    make_directory(root)
    path = contained(root, identity)
    if staged is not None:
        write(staged / "plan.json", raw)
        src_fd = file_exchange.parent_fd(staged.parent, staged.name)
        try:
            dst_fd = file_exchange.parent_fd(root, identity)
            try:
                file_exchange.rename(src_fd, staged.name, dst_fd, identity, exchange=False)
            finally:
                os.close(dst_fd)
        finally:
            os.close(src_fd)
        return Plan(identity, path, value, "planned")
    path.mkdir(mode=0o700)
    sync_directory(root)
    write(path / "plan.json", raw)
    write(path / "candidate.md", candidate)
    return Plan(identity, path, value, "planned")


def finish(plan: Plan, outcome: str) -> None:
    if outcome not in {"applied", "not-applied"}:
        raise IntegrityError("invalid MOC file outcome")
    write(contained(plan.path, "outcome"), (outcome + "\n").encode())


def record_applied(config: Config, value: dict[str, Any], before: bytes) -> Plan:
    """Record an already verified placement exchange; do not mint a different candidate inode."""
    validate(value, value["vault_id"], value["moc_id"])
    if hashlib.sha256(before).hexdigest() != value["before_sha256"]:
        raise IntegrityError("MOC placement before evidence changed")
    items = plans(config, value["vault_id"], value["moc_id"])
    if len(items) >= MAX_PLANS:
        raise IntegrityError("MOC write plan limit exceeded")
    raw = canonical(value).encode()
    identity = hashlib.sha256(raw).hexdigest()
    root = directory(config, value["vault_id"], value["moc_id"])
    make_directory(root)
    path = contained(root, identity)
    if path.exists():
        if (
            read(path / "plan.json") != raw
            or read(path / "candidate.md") != before
            or read(path / "outcome") != b"applied\n"
        ):
            raise IntegrityError("MOC placement journal changed")
        return Plan(identity, path, value, "applied")
    staged = Path(tempfile.mkdtemp(prefix=".moc-placement-", dir=config.state_root))
    write(staged / "plan.json", raw)
    write(staged / "candidate.md", before)
    write(staged / "claim", b"claimed\n")
    finish(Plan(identity, staged, value, "pending"), "applied")
    # Publish the complete journal atomically; interrupted staging cannot poison the chain.
    src_fd = file_exchange.parent_fd(staged.parent, staged.name)
    try:
        dst_fd = file_exchange.parent_fd(root, identity)
        try:
            file_exchange.rename(src_fd, staged.name, dst_fd, identity, exchange=False)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)
    return Plan(identity, path, value, "applied")
