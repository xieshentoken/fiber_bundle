"""One current entity table shared by cloud projection and search."""

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .. import auth, document_creation_save, publication_locations, vault_merge_analysis
from ..config import Config, IntegrityError
from ..inputs import canonical
from ..vault_scope import for_vault, inside, prefix
from . import cloud_cache, cloud_ledger, cloud_missing_publications, cloud_sources
from .contracts import fingerprint

MAX_ENTITIES = 30_000
MAX_MEMBERSHIPS = 100_000


@dataclass(frozen=True)
class Table:
    nodes: dict[str, dict[str, Any]]
    aliases: dict[str, str]
    paths: dict[str, str]
    ledger: cloud_ledger.Ledger | None
    token: str
    omitted_memberships: int = 0
    moc_paths: dict[str, str] = field(default_factory=dict)
    classification_complete: bool = True
    missing_proof: cloud_missing_publications.Proof | None = None


def _base(value: dict[str, Any], entity: str | None = None) -> dict[str, Any]:
    return {
        **value,
        "aliases": [],
        "availability": "current",
        "historical": False,
        "metadata_visible": True,
        "entity_id": entity or value["id"],
    }


def build(
    config: Config, db: sqlite3.Connection, client: str, vault: str, source: cloud_sources.Snapshot
) -> Table:
    if any(
        item.get("reason") == "quality_scope_requires_verified_projection"
        for item in source.coverage.get("unavailable", [])
    ):
        return Table({}, {}, {}, None, source.token)
    ledger = cloud_ledger.read(config, vault)
    nodes = {
        cloud_sources.identity(vault, note): _base(cloud_sources.wire(vault, note))
        for note in source.notes.values()
    }
    paths = {node["path"]: key for key, node in nodes.items()}
    policy = db.execute(
        "SELECT json_extract(value,'$.settings.moc_dir') FROM vault_save_policies WHERE vault_id=?",
        (vault,),
    ).fetchone()
    moc_dir = str(policy[0]) if policy and policy[0] else "Map"
    for node in nodes.values():
        if node["path"].startswith(moc_dir + "/") and re.search(
            r"(?i)\bmoc$", PurePosixPath(node["path"]).stem
        ):
            node["kind"] = "moc"
    typed_mocs, classification_complete = cloud_cache.moc_paths(
        config,
        vault,
        {
            path
            for path in source.notes
            if path.startswith(moc_dir + "/") and nodes[paths[path]]["kind"] != "moc"
        },
    )
    for path in typed_mocs:
        nodes[paths[path]]["kind"] = "moc"
    aliases = {key: key for key in nodes}
    for path, note in source.notes.items():
        entity = note.identity or (
            "publication:" + note.publication_id
            if note.publication_id
            else "note:"
            + str(
                uuid5(
                    NAMESPACE_URL,
                    "fiber-organization-note:" + vault + ":" + canonical(note.file_key or [path]),
                )
            )
        )
        aliases[entity] = paths[path]
        nodes[paths[path]]["entity_id"] = entity
    root_key = (source.root.stat().st_dev, source.root.stat().st_ino)
    # A changed filename/title is never identity evidence. Only the registered
    # physical key plus exact SHA can resolve an otherwise unregistered source.
    physical: dict[Any, list[Any]] = defaultdict(list)
    competing: dict[Any, list[str]] = defaultdict(list)
    for note in source.notes.values():
        if note.file_key:
            physical[(note.file_key, note.sha256)].append(note)
    for identity, record in ledger.registrations.items():
        if record.vault_key == root_key:
            competing[(record.file_key, record.sha256)].append(identity)
    for identity, record in ledger.registrations.items():
        if record.vault_key != root_key:
            continue
        key = (record.file_key, record.sha256)
        matched = physical.get(key, [])
        if (
            len(matched) == 1
            and len(competing[key]) == 1
            and matched[0].identity in {None, identity}
            and matched[0].publication_id == record.publication_id
        ):
            aliases[identity] = paths[matched[0].relative_path]
    for metadata in source.experts.values():
        expert = metadata["id"]
        old = paths.get(metadata["path"])
        node = _base(
            {
                **{key: value for key, value in metadata.items() if key != "link_paths"},
                "kind": "expert",
                "sha256": None,
                "note_id": None,
            },
            expert,
        )
        if old:
            original = nodes.pop(old)
            node["sha256"] = original["sha256"]
            node["note_id"] = original["note_id"]
            node["aliases"] = sorted(set([old, *original["aliases"]]))
            for alias, target in list(aliases.items()):
                if target == old:
                    aliases[alias] = expert
        nodes[expert] = node
        paths[metadata["path"]] = expert
        for path in metadata.get("link_paths", []):
            if path in paths and paths[path] not in {old, expert}:
                raise IntegrityError("note cloud expert entry alias conflict")
            paths[path] = expert
        aliases[expert] = expert
    allowed = for_vault(source.root, auth.grant(db, client, vault, "query"))
    layout = publication_locations.project_inventory(db, vault)
    from ..organization.packages import roots
    from ..vault_merge_guard import retained_paths

    hidden = layout.hidden
    retained = retained_paths(db, vault)
    hidden_roots = layout.hidden_roots | roots(config, vault)
    analysis_roots, _ = document_creation_save.inventory_paths(db, vault)
    analysis_paths = {item["note_path"] for item in vault_merge_analysis.records(db, vault)}
    unresolved = {
        identity
        for record in ledger.records.values()
        for identity in record.members
        if identity not in aliases and identity in ledger.registrations
    }
    missing_proof = (
        cloud_missing_publications.read(
            config, db, client, vault, source.root, ledger.registrations, unresolved
        )
        if unresolved
        else None
    )

    def safe_path(path: str, identity: str) -> bool:
        return (
            prefix(path) == path
            and allowed.allows(path)
            and path not in retained
            and (
                path not in hidden
                or bool(missing_proof and missing_proof.visible.get(identity) == path)
            )
            and path not in analysis_paths
            and not any(inside(path, base) for base in hidden_roots | analysis_roots)
        )

    def missing(identity: str) -> str:
        key = "missing:" + fingerprint([vault, identity])
        if key in nodes:
            return key
        record = ledger.registrations.get(identity)
        safe = bool(
            record
            and identity.startswith("note:")
            and record.publication_id is None
            and record.vault_key == root_key
            and safe_path(record.relative_path, identity)
            and all(safe_path(path, identity) for path in ledger.history.get(identity, []))
        )
        path = record.relative_path if safe and record else ""
        title = PurePosixPath(path).stem if path else "关联目标不可用"
        if safe and record:
            previous = ledger.titles.get(identity)
            if previous and (previous["path"], previous["sha256"], previous["file_key"]) == (
                path,
                record.sha256,
                list(record.file_key),
            ):
                title = str(previous["title"] or title)
        nodes[key] = {
            "id": key,
            "kind": "unresolved",
            "label": title,
            "path": path,
            "sha256": None,
            "note_id": None,
            "aliases": [identity] if safe else [],
            "availability": "missing",
            "historical": True,
            "metadata_visible": safe,
            "entity_id": identity if safe else None,
            "reason": (
                "publication_missing_confirmed"
                if safe and missing_proof and identity in missing_proof.visible
                else "identity_not_indexed"
                if safe
                else "target_unavailable"
            ),
        }
        return key

    omitted = 0
    seen = 0
    moc_paths = {}
    bound_paths: set[str] = set()
    for mid, record in ledger.records.items():
        plan = ledger.plans.get(mid)
        if (
            record.vault_dev != root_key[0]
            or record.vault_ino != root_key[1]
            or not plan
            or plan["vault_key"] != list(root_key)
            or plan["path"] not in paths
        ):
            omitted += len(record.members)
            continue
        moc = paths[plan["path"]]
        if plan["path"] in bound_paths:
            raise IntegrityError("note cloud duplicate moc binding")
        bound_paths.add(plan["path"])
        moc_paths[mid] = plan["path"]
        if nodes[moc]["kind"] == "expert":
            raise IntegrityError("note cloud moc expert identity conflict")
        nodes[moc]["kind"] = "moc"
        for identity in record.members:
            seen += 1
            if seen > MAX_MEMBERSHIPS or len(nodes) >= MAX_ENTITIES and identity not in aliases:
                omitted += 1
                continue
            if identity not in aliases:
                aliases[identity] = missing(identity)
    for alias, target in aliases.items():
        node = nodes[target]
        if alias != target and node["metadata_visible"]:
            node["aliases"] = sorted(set([*node["aliases"], alias]))
    token = fingerprint(
        [
            source.token,
            ledger.token,
            nodes,
            aliases,
            omitted,
            classification_complete,
            missing_proof.token if missing_proof else None,
        ]
    )
    return Table(
        nodes,
        aliases,
        paths,
        ledger,
        token,
        omitted,
        moc_paths,
        classification_complete,
        missing_proof,
    )


def statistics(table: Table) -> dict[str, Any]:
    experts = sum(node["kind"] == "expert" for node in table.nodes.values())
    unresolved = sum(node["kind"] == "unresolved" for node in table.nodes.values())
    notes = len(table.nodes) - experts - unresolved
    return {
        "current_notes": notes,
        "current_experts": experts,
        "current_entities": notes + experts,
        "unresolved_targets": unresolved,
        "has_unresolved": unresolved > 0,
        "targets_resolved": unresolved == 0,
        "indexed_notes": len(table.nodes) - experts - unresolved,
        "total_entities": len(table.nodes),
        "classification_complete": table.classification_complete,
    }
