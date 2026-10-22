"""Register native expert packages without executing scripts or rewriting their payload."""

import json
import sqlite3
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .. import auth, vault_directories
from ..config import Config, IntegrityError, InvalidInput
from ..inputs import canonical
from ..objects import digest
from ..publication_file_io import read
from ..storage_read_scope import audited
from . import store

CATALOG = "references/runtime/catalog.json"


def context(
    config: Config, db: sqlite3.Connection, client: str, vault: str, *, write: bool = False
) -> Any:
    from ..agent_connections.visibility import scope

    scope(config, db, client, [vault])
    auth.grant(db, client, vault, "query")
    if write:
        auth.grant(db, client, vault, "add")
    root, allowed, _ = vault_directories.vault_root(config, db, vault)
    # The copied L2 vault inherits the same user protection as its original.
    if root.name == "L2":
        from dataclasses import replace

        allowed = replace(
            allowed, exclude=tuple(sorted(set(allowed.exclude) | {"01-Projects/GF_supply_chain"}))
        )
    return root, allowed


def scoped_read(root: Any, allowed: Any, path: Any) -> Any:
    if not allowed.allows(path):
        raise auth.Denied(404)
    return read(root, path)[0]


@audited
def inspect(config: Config, db: sqlite3.Connection, client: str, vault: str, relative: str) -> Any:
    root, allowed = context(config, db, client, vault, write=True)
    raw = scoped_read(root, allowed, relative + "/manifest.json")
    try:
        manifest = json.loads(raw)
        package = manifest["package"]
        if (
            manifest["schema_version"] != "tkc.expert-skill/v0.1"
            or manifest["build"]["compiler"] != "fake-expert"
        ):
            raise ValueError()
        if package["status"] != "draft" or manifest["capabilities"].get("reference") is not True:
            raise ValueError()
        declared = manifest["integrity"]["files"]
        if not isinstance(declared, dict) or not {"SKILL.md", CATALOG} <= set(declared):
            raise ValueError()
        files = {"manifest.json": {"sha256": digest(raw), "byte_count": len(raw)}}
        for name, expected in declared.items():
            data = scoped_read(root, allowed, relative + "/" + name)
            if digest(data) != expected:
                raise IntegrityError("expert_package_member_changed")
            files[name] = {"sha256": expected, "byte_count": len(data)}
        catalog = json.loads(scoped_read(root, allowed, relative + "/" + CATALOG))
        if catalog.get("schema_version") not in {
            "tkc.runtime-catalog/v0.3",
            "tkc.runtime-catalog/v0.2",
        }:
            raise ValueError()
        if not isinstance(catalog.get("objects"), list) or not isinstance(
            catalog.get("figures", []), list
        ):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise InvalidInput("expert_package_protocol_incompatible") from exc
    text_snapshot = {
        name: scoped_read(root, allowed, relative + "/" + name).decode("utf-8")
        for name in {
            "manifest.json",
            CATALOG,
            "references/knowledge/index.json",
            "references/evidence/anchors.jsonl",
        }
        & files.keys()
    }
    version = digest(canonical(files).encode())
    lineage = {k: manifest.get(k) for k in ("sources",)}
    lineage["package_id"] = package["id"]
    identity = str(
        uuid5(
            NAMESPACE_URL,
            "fiber-external-expert:" + vault + ":" + digest(canonical(lineage).encode()),
        )
    )
    info = root.stat()
    return {
        "id": identity,
        "module_id": "expert:" + identity,
        "vault_id": vault,
        "path": relative,
        "title": package.get("name", package["id"]),
        "origin": "external",
        "version": version,
        "package_version": package.get("version"),
        "package_id": package["id"],
        "files": files,
        "manifest": manifest,
        "text_snapshot": text_snapshot,
        "vault_key": [info.st_dev, info.st_ino],
        "kind": "expert",
        "artifact_stage": "draft",
        "review_state": "deferred",
        "knowledge_verified": False,
        "independent_evidence": False,
    }


def register(config: Config, db: sqlite3.Connection, client: str, vault: str, relative: str) -> Any:
    value = inspect(config, db, client, vault, relative)
    return commit(config, db, client, vault, value)


def commit(config: Config, db: sqlite3.Connection, client: str, vault: str, value: Any) -> Any:
    """Commit an inspected snapshot; caller rechecks files when inspection was outside lock."""
    context(config, db, client, vault, write=True)
    relative = value["path"]
    previous = store.get(config, "packages", value["id"])
    # Registering a copy does not replace a still-visible active location.
    if previous and previous["path"] != relative:
        root, allowed = context(config, db, client, vault, write=True)
        try:
            scoped_read(root, allowed, previous["path"] + "/manifest.json")
        except FileNotFoundError:
            pass
        else:
            if previous["version"] == value["version"]:
                return public(previous)
            raise IntegrityError("expert_package_location_conflict")
    if previous != value:
        store.put(config, "versions", value["id"] + ":" + value["version"], vault, value)
        store.put(config, "packages", value["id"], vault, value)
        from .jobs import notify

        notify(config, vault, "package", value["path"], value["version"])
    return public(value)


def public(value: Any) -> Any:
    return {
        k: v
        for k, v in value.items()
        if k not in {"files", "manifest", "vault_key", "text_snapshot"}
    }


def roots(config: Config, vault: str) -> set[str]:
    return {p["path"] for p in store.rows(config, "packages", vault)}


def listing(
    config: Config, db: sqlite3.Connection, client: str, vault: str
) -> list[dict[str, Any]]:
    _, quality = __import__("fiber_bundle.agent_connections.visibility", fromlist=["scope"]).scope(
        config, db, client, [vault]
    )
    _, allowed = context(config, db, client, vault)
    if quality != "all":
        return []
    return [
        public(p)
        for p in store.rows(config, "packages", vault)
        if allowed.allows(p["path"] + "/manifest.json")
    ]


def load(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    identity: str,
    expected: Any = None,
    *,
    file_names: Any = None,
    text_only: bool = False,
) -> Any:
    value = store.get(config, "packages", identity)
    if value is None:
        raise auth.Denied(404)
    historical = expected is not None and expected != value["version"]
    if historical:
        value = store.get(config, "versions", identity + ":" + expected)
        if value is None or not text_only:
            raise InvalidInput("knowledge_version_stale")
    from ..agent_connections.visibility import scope

    _, quality = scope(config, db, client, [value["vault_id"]])
    if quality != "all":
        raise auth.Denied(404)
    root, allowed = context(config, db, client, value["vault_id"])
    if not allowed.allows(value["path"] + "/manifest.json"):
        raise auth.Denied(404)
    info = root.stat()
    if [info.st_dev, info.st_ino] != value["vault_key"]:
        raise IntegrityError("expert_package_vault_changed")
    names = (
        {
            "manifest.json",
            CATALOG,
            "references/knowledge/index.json",
            "references/evidence/anchors.jsonl",
        }
        if text_only
        else set(value["files"])
    )
    if file_names is not None:
        names = {"manifest.json"} | file_names
    files = {}
    for name in names & value["files"].keys():
        raw = (
            value["text_snapshot"][name].encode()
            if historical
            else scoped_read(root, allowed, value["path"] + "/" + name)
        )
        bound = value["files"][name]
        if digest(raw) != bound["sha256"] or len(raw) != bound["byte_count"]:
            raise IntegrityError("knowledge_module_changed")
        files[name] = raw
    catalog = json.loads(files[CATALOG]) if CATALOG in files else None
    objects = (catalog or {}).get("objects", [])
    return {
        **public(value),
        "entry_note_path": value["path"],
        "source_version_id": None,
        "objects": objects,
        "object_count": len(objects),
        "catalog": catalog,
        "anchors": [
            json.loads(line)
            for line in files.get("references/evidence/anchors.jsonl", b"").splitlines()
        ],
        "files": files,
        "runtime": {},
        "package_sha256": value["version"],
        "completeness_status": "unknown",
        "known_gaps": ["external_source_required_for_full_verification"],
        "save_basis": "external_skill_registration",
        "host_reference_adapter": True,
    }


def search(config: Config, db: sqlite3.Connection, client: str, vault: str, query: str) -> Any:
    from ..search_matching import best_passage, terms

    patterns = terms(query)
    results = []
    for item in listing(config, db, client, vault):
        module = load(config, db, client, item["id"], item["version"], text_only=True)
        text = "\n".join(str(o.get("statement") or o.get("title") or "") for o in module["objects"])
        best = best_passage(item["title"], text, patterns)
        if best:
            results.append(
                {
                    "vault_id": vault,
                    "note_path": item["path"],
                    "title": item["title"],
                    "sha256": item["version"],
                    "line": best[1],
                    "snippet": best[2],
                    "line_end": best[1] + len(best[2].splitlines()) - 1,
                    "source_kind": "agent_analysis",
                    "source_label": "外部专家 Skill Draft（未经独立核验）",
                    "known_gaps": ["external_source_required_for_full_verification"],
                    "save_basis": "external_skill_registration",
                    "expert_projection": True,
                    "expert_module_id": item["module_id"],
                    "expert_module_version": item["version"],
                    "artifact_stage": "draft",
                    "review_state": "deferred",
                    "knowledge_verified": False,
                    "independent_evidence": False,
                    "completeness_status": "unknown",
                }
            )
    return results


def verify_evidence(
    config: Config, db: sqlite3.Connection, client: str, source: dict[str, Any]
) -> dict[str, Any]:
    identity = source["expert_module_id"][7:]
    active = store.get(config, "packages", identity)
    if active is None or active["version"] != source["expert_module_version"]:
        raise InvalidInput("knowledge_version_stale")
    module = load(config, db, client, identity, active["version"], text_only=True)
    text = "\n".join(str(o.get("statement") or o.get("title") or "") for o in module["objects"])
    start, end = source.get("line"), source.get("line_end")
    if type(start) is not int or type(end) is not int or not 1 <= start <= end:
        raise InvalidInput("agent_evidence_invalid")
    return {
        **source,
        "vault_id": module["vault_id"],
        "note_path": module["path"],
        "title": module["title"],
        "sha256": module["version"],
        "snippet": "\n".join(text.splitlines()[start - 1 : end]),
        "source_kind": "agent_analysis",
        "source_label": "外部专家 Skill Draft（未经独立核验）",
        "known_gaps": ["external_source_required_for_full_verification"],
        "save_basis": "external_skill_registration",
        "artifact_stage": "draft",
        "review_state": "deferred",
        "knowledge_verified": False,
        "independent_evidence": False,
        "completeness_status": "unknown",
    }
