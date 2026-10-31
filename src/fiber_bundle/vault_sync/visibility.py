"""Path-scoped visibility evidence; registration of one note cannot evict unrelated notes."""

import hashlib
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .. import note_identity_store, publication_locations, vault_scope, vaults
from ..config import Config
from ..inputs import canonical


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Proof:
    context: str
    paths: dict[str, str]

    @property
    def token(self) -> str:
        return digest([self.context, self.paths])


def read(config: Config, db: sqlite3.Connection, vault: sqlite3.Row) -> Proof:
    root = vaults.checked_vault(config, vault)
    scope = vault_scope.for_vault(root, vault)
    from ..attachment_projection import hidden_roots
    from ..document_creation_save import inventory_paths
    from ..organization.packages import roots
    from ..vault_merge_guard import retained_paths

    layout = publication_locations.project_inventory(db, vault["id"], include_attachments=True)
    context = digest(
        {
            "root": str(root),
            "scope": scope.wire(),
            "experts": sorted(roots(config, vault["id"])),
            "hidden_roots": sorted(
                layout.hidden_roots
                | hidden_roots(db, vault["id"], location_roots=layout.attachment_roots)
            ),
            "retained": sorted(retained_paths(db, vault["id"])),
        }
    )
    values: dict[str, list[Any]] = defaultdict(list)
    for path, value in sorted(layout.managed.items()):
        values[path].append(["managed", value])
    for path in sorted(layout.hidden):
        values[path].append(["hidden"])
    queries = {
        "locations": "SELECT entry_note_path,kind,publication_id,state,package_root,revision "
        "FROM publication_locations WHERE vault_id=? ORDER BY kind,publication_id",
        "articles": "SELECT p.relative_path,p.id,p.state,p.version_id FROM article_publications p "
        "LEFT JOIN publication_locations l ON l.kind='article' AND l.publication_id=p.id "
        "WHERE COALESCE(l.vault_id,p.vault_id)=? ORDER BY p.id",
        "documents": "SELECT p.relative_path,p.run_id,p.state FROM document_publications p "
        "JOIN execution_runs r ON r.id=p.run_id WHERE r.vault_id=? ORDER BY p.run_id",
        "media": "SELECT p.relative_path,p.job_id,p.state FROM media_publications p "
        "JOIN media_tasks m ON m.id=p.job_id WHERE m.vault_id=? ORDER BY p.job_id",
        "expert_publications": "SELECT p.relative_path,p.task_id,p.state "
        "FROM source_expert_publications p "
        "JOIN source_imports i ON i.id=p.task_id WHERE i.vault_id=? ORDER BY p.task_id",
    }
    for kind, sql in queries.items():
        for row in db.execute(sql, (vault["id"],)):
            values[row[0]].append([kind, *tuple(row)[1:]])
    for r in note_identity_store.records(config, vault["id"]):
        values[r.relative_path].append(
            ["identity", r.identity, r.publication_id, r.vault_key, r.file_key]
        )
    analysis, published = inventory_paths(db, vault["id"])
    for path in sorted(analysis | published):
        values[path].append(["analysis", path in published])
    return Proof(context, {path: digest(value) for path, value in values.items()})


def changed(before: Proof | None, after: Proof, observed: set[str]) -> set[str]:
    if before is None or before.context != after.context:
        return observed
    prefixes = {
        p
        for p in before.paths.keys() | after.paths.keys()
        if before.paths.get(p) != after.paths.get(p)
    }
    return {p for p in observed if any(p == q or p.startswith(q + "/") for q in prefixes)}


def previous(index: sqlite3.Connection) -> Proof | None:
    index.execute(
        "CREATE TABLE IF NOT EXISTS sync_projection(path TEXT PRIMARY KEY, proof TEXT NOT NULL)"
    )
    values = dict(index.execute("SELECT path,proof FROM sync_projection"))
    context = values.pop("", None)
    return Proof(context, values) if context else None


def save(index: sqlite3.Connection, proof: Proof) -> None:
    index.execute("DELETE FROM sync_projection")
    index.executemany(
        "INSERT INTO sync_projection VALUES(?,?)", [("", proof.context), *proof.paths.items()]
    )
