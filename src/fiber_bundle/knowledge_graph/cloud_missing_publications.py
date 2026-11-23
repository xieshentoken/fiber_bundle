"""Narrow historical disclosure for explicitly confirmed missing intake entries.

Only compact JSON metadata is selected. Recovery outputs, original files and
availability commands are never opened, decoded or executed by this reader.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import auth, document_creation_save, note_changes, publication_locations
from ..agent_connections.visibility import scope
from ..config import Config, InvalidInput
from ..inputs import canonical
from ..note_identity_store import Registration
from ..vault_scope import Scope, for_vault, prefix
from .contracts import fingerprint


@dataclass(frozen=True)
class Proof:
    visible: dict[str, str]
    targets: frozenset[str]
    token: str


FIELDS = (
    "availability",
    "root_key",
    "mapping",
    "body_files",
    "file_keys",
    "published_hashes",
    "identity_overrides",
    "retained_source_root",
    "intake_source.source_version_id",
    "intake_source.receipt_id",
    "intake_source.source_receipt_id",
)
OP_FIELDS = (
    "vault_id",
    "kind",
    "publication_id",
    "entry_note_path",
    "root_key",
    "missing_paths",
    "revision",
    "location_sha256",
)


def _identifier(value: Any) -> bool:
    try:
        note_changes.identifier(value)
        return True
    except (InvalidInput, TypeError):
        return False


def _path(allowed: Scope, value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return prefix(value) == value and allowed.allows(value)
    except InvalidInput:
        return False


def read(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    root: Path,
    registrations: dict[str, Registration],
    targets: set[str] | frozenset[str],
) -> Proof:
    requested = frozenset(targets)
    row = auth.grant(db, client, vault, "query")
    if scope(config, db, client, [vault])[1] != "all":
        return Proof({}, requested, fingerprint(["quality_restricted"]))
    allowed = for_vault(root, row)
    info = root.stat()
    root_key = [info.st_dev, info.st_ino]
    eligible: dict[str, Registration] = {}
    for identity in sorted(requested):
        record = registrations.get(identity)
        if (
            record
            and identity.startswith("note:")
            and record.publication_id is None
            and list(record.vault_key) == root_key
            and prefix(record.relative_path) == record.relative_path
            and allowed.allows(record.relative_path)
        ):
            eligible[identity] = record
    if not eligible:
        return Proof({}, requested, fingerprint([root_key, []]))
    expressions = ",".join("'$." + name + "'" for name in FIELDS)
    locations = []
    for raw in db.execute(
        "SELECT kind,publication_id,vault_id,entry_note_path,package_root,revision,state,"
        f"json_extract(value,{expressions}) AS projected FROM publication_locations "
        "WHERE vault_id=? AND kind='intake' AND entry_note_path IN "
        "(SELECT value FROM json_each(?)) ORDER BY publication_id",
        (vault, canonical(sorted({record.relative_path for record in eligible.values()}))),
    ):
        locations.append(
            {
                **{key: value for key, value in dict(raw).items() if key != "projected"},
                "metadata": dict(zip(FIELDS, json.loads(raw["projected"]), strict=True)),
            }
        )
    # Any additional publication claiming this path prevents the narrow hidden
    # exception, including a prepared/conflicting publication of another kind.
    claims: dict[str, list[tuple[str, str]]] = {}
    claim_proof = []
    for raw in db.execute(
        "SELECT l.kind,l.publication_id,l.revision,l.state,m.value AS path "
        "FROM publication_locations l,json_each(l.value,'$.mapping') m WHERE l.vault_id=? "
        "AND m.value IN (SELECT value FROM json_each(?)) ORDER BY l.kind,l.publication_id,m.key",
        (vault, canonical(sorted({record.relative_path for record in eligible.values()}))),
    ):
        claims.setdefault(raw["path"], []).append((raw["kind"], raw["publication_id"]))
        claim_proof.append(tuple(raw))
    visible = {}
    evidence = []
    op_expressions = ",".join("'$." + name + "'" for name in OP_FIELDS)
    for location in locations:
        meta = location["metadata"]
        availability = meta["availability"] or {}
        operation_id = availability.get("operation_id") if isinstance(availability, dict) else None
        operation = (
            db.execute(
                "SELECT id,vault_id,kind,state,"
                f"json_extract(payload,{op_expressions}) AS projected "
                "FROM vault_operations WHERE id=?",
                (operation_id,),
            ).fetchone()
            if _identifier(operation_id)
            else None
        )
        op = (
            dict(zip(OP_FIELDS, json.loads(operation["projected"]), strict=True))
            if operation
            else {}
        )
        version = meta["intake_source.source_version_id"]
        sources = []
        if _identifier(version):
            sources = [
                tuple(value)
                for value in db.execute(
                    "SELECT r.job_id,r.state,r.ingress_scope,r.event_id,r.source_version_id,"
                    "v.source_id,v.version_no,v.manifest_digest,v.retention FROM receipts r "
                    "JOIN source_versions v ON v.id=r.source_version_id "
                    "WHERE r.source_version_id=? AND r.state='stored' ORDER BY r.job_id",
                    (version,),
                )
            ]
        source_visible = bool(
            sources and document_creation_save.archive_visible(db, client, version)
        )
        for field in ("intake_source.receipt_id", "intake_source.source_receipt_id"):
            recorded_receipt = meta[field]
            if recorded_receipt is not None and (
                not isinstance(recorded_receipt, str)
                or recorded_receipt not in {value[0] for value in sources}
            ):
                source_visible = False
        operation_proof = (
            {
                **{key: value for key, value in dict(operation).items() if key != "projected"},
                "metadata": op,
            }
            if operation
            else None
        )
        evidence.append([location, operation_proof, sources, source_visible])
        if (
            location["state"] != "reconcile"
            or not isinstance(availability, dict)
            or availability.get("state") != "missing"
            or not operation
            or operation["vault_id"] != vault
            or operation["kind"] != "publication_missing"
            or operation["state"] != "completed"
            or not source_visible
            or meta["identity_overrides"] not in (None, {})
            or meta["retained_source_root"] is not None
        ):
            continue
        path = location["entry_note_path"]
        publication = location["publication_id"]
        if not _identifier(publication):
            continue
        identity = publication_locations.note_id("intake", publication, "note.md")
        record = eligible.get(identity)
        mapping = meta["mapping"]
        hashes, keys = meta["published_hashes"], meta["file_keys"]
        if (
            record is None
            or record.relative_path != path
            or not isinstance(mapping, dict)
            or mapping.get("note.md") != path
            or not isinstance(hashes, dict)
            or not isinstance(keys, dict)
            or hashes.get(path) != record.sha256
            or keys.get(path) != list(record.file_key)
            or meta["root_key"] != root_key
            or claims.get(path) != [("intake", publication)]
        ):
            continue
        bodies = meta["body_files"] if meta["body_files"] is not None else mapping
        if (
            not isinstance(bodies, (dict, list))
            or not all(isinstance(logical, str) for logical in bodies)
            or not all(isinstance(value, str) for value in mapping.values())
        ):
            continue
        missing = sorted(
            value
            for logical, value in mapping.items()
            if logical.endswith(".md") and logical in bodies
        )
        if (
            path not in missing
            or any(not _path(allowed, value) for value in missing)
            or availability.get("missing_paths") != missing
            or op.get("missing_paths") != missing
            or op.get("vault_id") != vault
            or op.get("kind") != "intake"
            or op.get("publication_id") != publication
            or op.get("entry_note_path") != path
            or op.get("root_key") != root_key
            or type(op.get("revision")) is not int
            or location["revision"] != op["revision"] + 1
            or not isinstance(op.get("location_sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", op["location_sha256"]) is None
        ):
            continue
        # location_sha256 is retained historical CAS evidence, not a recomputed
        # checksum of today's recovery package. Its value participates in token.
        visible[identity] = path
    return Proof(visible, requested, fingerprint([root_key, evidence, claim_proof]))
