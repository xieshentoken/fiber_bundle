"""Scope-pruned entity and physical MOC snapshots; classification never loads images."""

import json
import re
import sqlite3
from pathlib import PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .. import auth, moc_registration, note_inventory, note_placement, vault_save_policy
from ..config import Config, InvalidInput, StorageError
from ..inputs import canonical
from ..objects import digest
from . import packages, text_features


def tags(text: str) -> list[str]:
    result = set(re.findall(r"(?<![\w/])#([^\s#\[\]{}(),，。:：]+)", text))
    match = re.match(r"\ufeff?---\r?\n(.*?)\r?\n---", text, re.S)
    if match:
        front = match[1]
        field = re.search(r"(?m)^tags:\s*(.*(?:\n[ \t]+[^\n]*)*)", front)
        if field:
            result.update(re.findall(r"[\w\u3400-\u9fff/-]+", field[1]))
    return sorted(result)


def snapshot(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    scope_packages: bool = False,
) -> Any:
    root, allowed = packages.context(config, db, client, vault)
    row = auth.grant(db, client, vault, "query")
    settings = vault_save_policy.get(config, db, vault)["settings"]
    moc_dir = settings.get("moc_dir", "Map")
    if include is not None:
        from typing import cast

        from ..vault_scope import inside

        selected = []
        for desired in [*include, moc_dir]:
            for base in allowed.include:
                if inside(desired, base):
                    selected.append(desired)
                elif inside(base, desired):
                    selected.append(base)
        narrowed = dict(row)
        narrowed["scope_json"] = json.dumps(
            {
                "include": sorted(set(selected)),
                "exclude": sorted(set(allowed.exclude) | set(exclude or [])),
            }
        )
        row = cast(sqlite3.Row, narrowed)
    inventory = note_inventory.read(
        config, db, row, exclude_path=lambda path: text_features.excluded(path, settings)
    )
    bindings = moc_registration.bindings(config, vault)
    records = note_placement.records(config, vault)
    mocs = []
    for note in inventory.notes:
        if not allowed.allows(note.relative_path):
            continue
        if not note.relative_path.startswith(moc_dir + "/") or not (
            re.search(r"(?im)^type:\s*moc\s*$", note.text)
            or re.search(r"(?i)\bmoc$", PurePosixPath(note.relative_path).stem)
        ):
            continue
        mid = bindings.get(note.relative_path) or str(
            uuid5(NAMESPACE_URL, "fiber-moc-adoption:" + vault + ":" + note.relative_path)
        )
        record = records.get(mid)
        mocs.append(
            {
                "id": mid,
                "path": note.relative_path,
                "title": note.title,
                "sha256": note.sha256,
                "text": note.text,
                "registered": record is not None,
                "members": record.members if record else {},
                "revision": record.revision if record else 0,
            }
        )
    entities = []
    for note in inventory.notes:
        path = note.relative_path
        if not allowed.allows(path) or path.startswith(moc_dir + "/"):
            continue
        identity = note.identity or (
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
        entities.append(
            {
                "id": identity,
                "kind": "note",
                "vault_id": vault,
                "path": path,
                "title": note.title,
                "sha256": note.sha256,
                "text": note.text,
                "tags": tags(note.text),
                "file_key": list(note.file_key) if note.file_key else None,
                "publication_id": note.publication_id,
                "_inventory_identity": note.identity,
                "source": _source(db, vault, path),
                "version": note.sha256,
            }
        )
    for item in packages.listing(config, db, client, vault):
        if scope_packages:
            from ..vault_scope import inside

            if not any(inside(item["path"], p) for p in include or []) or any(
                inside(item["path"], p) for p in exclude or []
            ):
                continue
        try:
            module = packages.load(config, db, client, item["id"], item["version"], text_only=True)
        except (StorageError, OSError):
            # Retain the registered version; the Book scanner reports the broken package.
            # One incomplete copy must not take the ordinary-note workspace down.
            continue
        features = text_features.expert(module)
        entities.append(
            {
                "id": item["module_id"],
                "kind": "expert",
                "vault_id": vault,
                "path": item["path"],
                "title": item["title"],
                "sha256": item["version"],
                "version": item["version"],
                "text": features["text"],
                "sections": features["sections"],
                "domain": features["domain"],
                "concepts": features["concepts"],
                "tags": [item.get("package_id", "")],
                "source": "external",
            }
        )
    # Fiber packages already have a published entry. Project them once as a native package entity.
    from .. import published_expert

    for row in db.execute(
        "SELECT p.task_id FROM source_expert_publications p JOIN source_imports i "
        "ON i.id=p.task_id LEFT JOIN publication_locations l ON l.kind='expert' "
        "AND l.publication_id=p.task_id WHERE COALESCE(l.vault_id,i.vault_id)=? "
        "AND p.state='published'",
        (vault,),
    ):
        module = published_expert.text(config, db, client, row[0])
        features = text_features.expert(module)
        entities = [e for e in entities if e.get("publication_id") != row[0]]
        entities.append(
            {
                "id": module["module_id"],
                "kind": "expert",
                "vault_id": vault,
                "path": module["entry_note_path"],
                "title": module["entry_note_path"].split("/")[-2],
                "sha256": module["version"],
                "version": module["version"],
                "text": features["text"],
                "sections": features["sections"],
                "domain": features["domain"],
                "concepts": features["concepts"],
                "tags": [],
                "source": "fiber",
            }
        )
    from collections import Counter

    from . import note_moc_sync, store

    projections = {r["entity"]: r for r in store.rows(config, "note_moc", vault)}
    for entity in entities:
        if entity["kind"] == "note":
            note_moc_sync.decorate(entity, projections.get(entity["id"]))

    stems = Counter(PurePosixPath(e["path"]).stem for e in entities)
    for entity in entities:
        entity["ambiguous_title"] = stems[PurePosixPath(entity["path"]).stem] > 1
    catalog_hash = digest(
        canonical(
            [
                {
                    "id": m["id"],
                    "path": m["path"],
                    "title": m["title"],
                    "manual": re.sub(
                        r"<!-- fiber-moc:.*?<!-- fiber-moc:.*?end -->", "", m["text"], flags=re.S
                    ).rstrip(),
                }
                for m in mocs
            ]
        ).encode()
    )
    from ..knowledge_graph import policy as graph_policy
    from . import model_review

    graph = graph_policy.current(config)
    model = None
    try:
        model = model_review.route(config, db, vault).digest
    except InvalidInput:
        pass
    return (
        entities,
        mocs,
        {
            "catalog_sha256": catalog_hash,
            "scope": allowed.wire(),
            "semantic_policy": graph.digest if graph else None,
            "model_policy": model,
        },
    )


def _source(db: sqlite3.Connection, vault: str, path: Any) -> Any:
    # Source labels are metadata, not inferred by a model.
    for row in db.execute(
        "SELECT kind,publication_id,entry_note_path FROM publication_locations "
        "WHERE vault_id=? AND entry_note_path=?",
        (vault, path),
    ):
        return row["kind"]
    return "note"


def manual_memberships(entity: Any, mocs: Any) -> list[str]:
    from .manual_relations import ManualRelations

    return ManualRelations(mocs).memberships(entity)


def public_entity(e: Any) -> Any:
    return {
        k: v
        for k, v in e.items()
        if not k.startswith("_") and k not in {"text", "file_key", "sections", "concepts"}
    }
