"""Freeze already-journaled missing-member placeholders; never admit a missing source."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from . import (
    cloud_files,
    moc_file_identity,
    moc_file_journal,
    moc_markdown,
    moc_store,
    note_changes,
    note_identities,
    note_identity_store,
    vault_scope,
)
from .cloud_observation import ObservationError
from .config import Config, IntegrityError
from .inputs import canonical
from .objects import digest
from .vault_sync import store as sync_store

SCHEMA = "moc-retained-members/v2"
MAX_MEMBERS = 20
MAX_BYTES = 24 * 1024
PROOF_FIELDS = {
    "moc_id",
    "base_id",
    "base_revision",
    "managed_sha256",
    "registration",
    "owners",
    "line",
}


def _registration(value: Any) -> note_identity_store.Registration:
    if not isinstance(value, dict) or set(value) != set(
        note_identity_store.Registration.__dataclass_fields__
    ):
        raise IntegrityError("moc_retained_member_invalid")
    key = value["vault_key"]
    if not isinstance(key, list) or len(key) != 2 or any(type(v) is not int or v < 0 for v in key):
        raise IntegrityError("moc_retained_member_invalid")
    result = note_identity_store.Registration(
        **{
            **value,
            "file_key": moc_file_identity.file_key(value["file_key"]),
            "vault_key": tuple(key),
        }
    )
    note_identities.validate(result)
    if result.publication_id is not None or not result.identity.startswith("note:"):
        raise IntegrityError("moc_retained_member_invalid")
    return result


def _members(value: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(value, dict) or not value:
        raise IntegrityError("moc_retained_member_invalid")
    if "schema" not in value:
        members, blocks = value, None
    else:
        if (
            set(value) != {"schema", "blocks", "members"}
            or value["schema"] != SCHEMA
            or not isinstance(value["blocks"], dict)
            or not 1 <= len(value["blocks"]) <= MAX_MEMBERS
        ):
            raise IntegrityError("moc_retained_member_invalid")
        members, blocks = value["members"], value["blocks"]
    if not isinstance(members, dict) or not 1 <= len(members) <= MAX_MEMBERS:
        raise IntegrityError("moc_retained_member_invalid")
    return members, blocks


def proofs(value: Any, *, target: str | None = None) -> dict[str, Any]:
    """Validate either wire format and resolve v2 block references for read-only consumers.

    Returning normalized copies never rewrites an old journal or changes its
    canonical bytes/digest. The persisted v2 budget counts a shared block once.
    """
    members, blocks = _members(value)
    result = {}
    references = set()
    for identity, proof in members.items():
        moc_store.stable_identity(identity)
        block_field = "block" if blocks is None else "block_ref"
        if not isinstance(proof, dict) or set(proof) != PROOF_FIELDS | {block_field}:
            raise IntegrityError("moc_retained_member_invalid")
        if blocks is None:
            block = proof["block"]
        else:
            reference = proof["block_ref"]
            if (
                not isinstance(reference, str)
                or not note_changes.DIGEST.fullmatch(reference)
                or reference != proof["managed_sha256"]
                or reference not in blocks
            ):
                raise IntegrityError("moc_retained_member_invalid")
            block = blocks[reference]
            references.add(reference)
        if not isinstance(block, str):
            raise IntegrityError("moc_retained_member_invalid")
        try:
            encoded = block.encode("utf-8")
        except UnicodeEncodeError as error:
            raise IntegrityError("moc_retained_member_invalid") from error
        note_changes.identifier(proof["moc_id"])
        if (
            not isinstance(proof["base_id"], str)
            or not note_changes.DIGEST.fullmatch(proof["base_id"])
            or not isinstance(proof["managed_sha256"], str)
            or not note_changes.DIGEST.fullmatch(proof["managed_sha256"])
            or type(proof["base_revision"]) is not int
            or proof["base_revision"] < 1
            or proof["owners"] not in (["manual"], ["system"], ["manual", "system"])
            or proof["line"] != f"- 当前不可见：{identity}（归属保留）"
            or len(encoded) > MAX_BYTES
            or digest(encoded) != proof["managed_sha256"]
            or block.splitlines().count(proof["line"]) != 1
            or _registration(proof["registration"]).identity != identity
        ):
            raise IntegrityError("moc_retained_member_invalid")
        result[identity] = {k: v for k, v in proof.items() if k != "block_ref"} | {"block": block}
    if blocks is not None and references != set(blocks):
        raise IntegrityError("moc_retained_member_invalid")
    size = len(canonical(value).encode())
    if size > MAX_BYTES:
        failure = ObservationError("moc_retained_member_limit", None, "retained_evidence_limit")
        failure.detail.update(
            phase="moc_retained_members",
            path=target,
            moc_id=next(iter(result.values()))["moc_id"],
            actual_bytes=size,
            limit_bytes=MAX_BYTES,
            missing_members=[p["registration"]["relative_path"] for p in result.values()],
        )
        raise failure
    return result


def validate(value: Any, *, target: str | None = None) -> dict[str, Any]:
    proofs(value, target=target)
    return cast(dict[str, Any], value)


def _missing(root: Path, scope: vault_scope.Scope, registration: Any) -> bool:
    path = registration.relative_path
    if not scope.allows(path) or any(p.startswith(".") for p in path.split("/")):
        raise IntegrityError("moc_retained_member_denied")
    # A missing parent, placeholder or access error is not proof of a missing
    # ordinary file. Preserve those failures rather than turning them into consent.
    cloud_files.require_local((root / path).parent)
    try:
        cloud_files.require_local(root / path)
    except FileNotFoundError:
        return True
    return False


def _unlocated(config: Config, vault: str, scope: vault_scope.Scope, registration: Any) -> None:
    with sync_store.connection(config) as side:
        if side is None:
            raise IntegrityError("moc_retained_member_observer_required")
        candidates = side.execute(
            "SELECT path,signature FROM files WHERE vault=? AND directory=0 AND path!=? "
            "AND json_extract(signature,'$[0]')=? AND json_extract(signature,'$[1]')=? LIMIT 10001",
            (vault, registration.relative_path, *registration.file_key[:2]),
        ).fetchall()
    if len(candidates) > 10000:
        raise IntegrityError("moc_retained_member_metadata_limit")
    if any(
        scope.allows(row["path"])
        and float(json.loads(row["signature"])[7]).hex() == registration.file_key[2]
        for row in candidates
    ):
        raise IntegrityError("moc_retained_member_relocated")


def _historical(config: Config, vault: str, mid: str, revision: int) -> Any:
    with moc_store.connection(config) as side:
        row = (
            side.execute(
                "SELECT record_json FROM events WHERE vault_id=? AND moc_id=? AND revision=?",
                (vault, mid, revision),
            ).fetchone()
            if side
            else None
        )
    if row is None:
        raise IntegrityError("moc_retained_member_history_unavailable")
    record = moc_store.Record(**json.loads(row[0]))
    moc_store.validate(record)
    return record


def capture(
    config: Config,
    vault: str,
    root: Path,
    scope: vault_scope.Scope,
    target: str,
    record: Any,
    data: bytes,
    mid: str,
) -> dict[str, Any]:
    if record is None or "- 当前不可见：".encode() not in data:
        return {}
    base = moc_file_journal.latest(moc_file_journal.plans(config, vault, mid))
    if base is None or base.value["path"] != target:
        return {}
    _, block, _ = moc_markdown.split(data, mid)
    if digest(block) != base.value["managed_sha256"]:
        return {}
    old = None
    registrations = {r.identity: r for r in note_identity_store.records(config, vault)}
    result = {}
    for identity, owners in record.members.items():
        registration = registrations.get(identity)
        line = f"- 当前不可见：{identity}（归属保留）"
        if (
            registration is None
            or registration.publication_id is not None
            or block.decode("utf-8-sig").splitlines().count(line) != 1
        ):
            continue
        if _missing(root, scope, registration):
            old = old or _historical(config, vault, mid, base.value["revision"])
            if old.members.get(identity) != owners:
                continue
            _unlocated(config, vault, scope, registration)
            result[identity] = {
                "moc_id": mid,
                "base_id": base.plan_id,
                "base_revision": base.value["revision"],
                "managed_sha256": base.value["managed_sha256"],
                "owners": owners,
                "registration": json.loads(canonical(asdict(registration))),
                "line": line,
                "block_ref": base.value["managed_sha256"],
            }
    return (
        validate(
            {
                "schema": SCHEMA,
                "blocks": {base.value["managed_sha256"]: block.decode("utf-8")},
                "members": result,
            },
            target=target,
        )
        if result
        else {}
    )


def check(
    config: Config,
    vault: str,
    root: Path,
    scope: vault_scope.Scope,
    target: str,
    value: dict[str, Any],
) -> set[str]:
    members = proofs(value, target=target)
    registrations = {r.identity: r for r in note_identity_store.records(config, vault)}
    paths = set()
    for identity, proof in members.items():
        plans = moc_file_journal.plans(config, vault, proof["moc_id"])
        moc_file_journal.latest(plans)  # Also reject a forked historical chain.
        base = next((p for p in plans if p.plan_id == proof["base_id"]), None)
        current = moc_store.load(config, vault, proof["moc_id"])
        old = _historical(config, vault, proof["moc_id"], proof["base_revision"])
        registration = _registration(proof["registration"])
        if (
            base is None
            or base.status != "applied"
            or base.value["path"] != target
            or base.value["revision"] != proof["base_revision"]
            or base.value["managed_sha256"] != proof["managed_sha256"]
            or current is None
            or current.members.get(identity) != proof["owners"]
            or old.members.get(identity) != proof["owners"]
            or registrations.get(identity) != registration
            or registration.vault_key != (root.stat().st_dev, root.stat().st_ino)
        ):
            raise IntegrityError("moc_retained_member_evidence_changed")
        if not _missing(root, scope, registration):
            raise IntegrityError("moc_retained_member_reappeared")
        _unlocated(config, vault, scope, registration)
        paths.add(registration.relative_path)
    return paths


def issues(config: Config, vault: str) -> list[dict[str, str]]:
    """Read only journaled attention; never probe or repair a Vault from a status poll."""
    from .note_placement import records

    result = []
    for mid in records(config, vault):
        latest = moc_file_journal.latest(moc_file_journal.plans(config, vault, mid))
        retained = latest.value.get("read_scope", {}).get("retained_members", {}) if latest else {}
        for identity, proof in (proofs(retained) if retained else {}).items():
            result.append(
                {
                    "identity": identity,
                    "moc_id": mid,
                    "path": proof["registration"]["relative_path"],
                    "reason": "moc_historical_member_missing",
                }
            )
            if len(result) >= 100:
                return result
    return result
