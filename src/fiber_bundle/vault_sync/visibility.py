"""Path-scoped visibility evidence; registration of one note cannot evict unrelated notes."""

import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .. import note_identity_store, publication_locations, vault_scope, vaults
from ..config import Config
from ..inputs import canonical


@dataclass(frozen=True)
class Legacy:
    context: str
    paths: dict[str, str]
    vault: str
    indexed: int


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Proof:
    context: str
    paths: dict[str, str]
    legacy: Legacy | None = field(default=None, compare=False, repr=False)
    published: str | None = field(default=None, compare=False, repr=False)

    @property
    def token(self) -> str:
        return digest([self.context, self.paths])


def _legacy_root(config: Config, vault: str, root: str, info: os.stat_result) -> int | None:
    """Bind compatibility to a completed generation's original root, not the latest scan."""
    from . import store

    with store.connection(config) as side:
        if side is None:
            return None
        row = side.execute(
            "SELECT root,baseline,indexed FROM libraries WHERE id=?", (vault,)
        ).fetchone()
        if row is None or row["root"] != root or not row["baseline"] or row["indexed"] < 0:
            return None
        old = side.execute(
            "SELECT new_signature FROM revisions WHERE vault=? AND path='' AND revision<=? "
            "ORDER BY revision DESC LIMIT 1",
            (vault, row["indexed"]),
        ).fetchone()
    try:
        signature = json.loads(old[0]) if old else []
        if len(signature) != 9 or [signature[i] for i in (0, 1, 2, 7, 8)] != [
            info.st_dev,
            info.st_ino,
            info.st_mode,
            getattr(info, "st_birthtime", 0),
            getattr(info, "st_flags", 0),
        ]:
            return None
    except (ValueError, TypeError, IndexError):
        return None
    return int(row["indexed"])


def read(config: Config, db: sqlite3.Connection, vault: sqlite3.Row) -> Proof:
    root = vaults.checked_vault(config, vault)
    scope = vault_scope.for_vault(root, vault)
    info = root.stat()
    from ..attachment_projection import hidden_roots
    from ..document_creation_save import inventory_paths
    from ..organization.packages import roots
    from ..vault_merge_guard import retained_paths

    layout = publication_locations.project_inventory(db, vault["id"], include_attachments=True)
    experts = sorted(roots(config, vault["id"]))
    hidden = sorted(
        layout.hidden_roots | hidden_roots(db, vault["id"], location_roots=layout.attachment_roots)
    )
    retained = sorted(retained_paths(db, vault["id"]))
    context = digest(
        {
            "root": str(root),
            "root_key": [info.st_dev, info.st_ino, info.st_mode, getattr(info, "st_flags", 0)],
            "scope": scope.wire(),
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
    paths = {path: digest(value) for path, value in values.items()}
    indexed = _legacy_root(config, vault["id"], str(root), info)
    legacy = (
        Legacy(
            digest(
                {
                    "root": str(root),
                    "scope": scope.wire(),
                    "experts": experts,
                    "hidden_roots": hidden,
                    "retained": retained,
                }
            ),
            paths.copy(),
            vault["id"],
            indexed,
        )
        if indexed is not None
        else None
    )
    markers: dict[str, list[Any]] = defaultdict(list)
    for kind, roots_ in (("expert_root", experts), ("hidden_root", hidden), ("retained", retained)):
        for path in roots_:
            markers[path].append([kind])
    for path, prefix in markers.items():
        paths[path] = digest(prefix + values.get(path, []))
    return Proof(context, paths, legacy=legacy)


def changed(before: Proof | None, after: Proof, observed: set[str]) -> set[str]:
    if before is None:
        return observed
    if before.context != after.context:
        legacy = after.legacy
        if (
            legacy is None
            or before.context != legacy.context
            or before.published != digest([legacy.vault, legacy.indexed, before.token])
        ):
            return observed
        # No guessed root/scope conversion: only the exact former context may bridge.
        return changed_paths(before, Proof(legacy.context, legacy.paths), observed)
    return changed_paths(before, after, observed)


def changed_paths(before: Proof | None, after: Proof, observed: set[str]) -> set[str]:
    """Query admission compares affected bindings; current scope/root is checked separately."""
    if before is None:
        return observed
    prefixes = {
        p
        for p in before.paths.keys() | after.paths.keys()
        if before.paths.get(p) != after.paths.get(p)
    }
    return {p for p in observed if any(p == q or p.startswith(q + "/") for q in prefixes)}


def previous_readonly(index: sqlite3.Connection) -> Proof | None:
    if not index.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_projection'"
    ).fetchone():
        return None
    values = dict(index.execute("SELECT path,proof FROM sync_projection"))
    context = values.pop("", None)
    if (
        not context
        or not index.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_metadata'"
        ).fetchone()
    ):
        return None
    proof = Proof(context, values)
    metadata = index.execute("SELECT visibility FROM sync_metadata LIMIT 2").fetchall()
    if len(metadata) != 1 or metadata[0][0] != proof.token:
        return None
    published = index.execute("SELECT fingerprint FROM generation LIMIT 2").fetchall()
    return Proof(context, values, published=published[0][0] if len(published) == 1 else None)


def previous(index: sqlite3.Connection) -> Proof | None:
    index.execute(
        "CREATE TABLE IF NOT EXISTS sync_projection(path TEXT PRIMARY KEY, proof TEXT NOT NULL)"
    )
    return previous_readonly(index)


def save(index: sqlite3.Connection, proof: Proof) -> None:
    index.execute("DELETE FROM sync_projection")
    index.executemany(
        "INSERT INTO sync_projection VALUES(?,?)", [("", proof.context), *proof.paths.items()]
    )
