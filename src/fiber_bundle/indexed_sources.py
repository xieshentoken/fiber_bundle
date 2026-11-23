"""Published index admission, localized to unchanged sources instead of a Vault generation.

The optional expert table is derived cache data. Only an index writer publishes it;
registration or reading never makes a Draft searchable in an observed Vault.
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import auth, note_index, note_search_cache, publication_locations, vault_scope, vaults
from .cloud_files import SF_DATALESS
from .config import Config, StorageError
from .inputs import canonical
from .objects import digest
from .vault_sync import store, visibility
from .vault_sync.scanner import signature

EXPERT_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS indexed_experts("
    "id TEXT PRIMARY KEY, version TEXT NOT NULL, path TEXT NOT NULL) STRICT"
)


def managed(config: Config, vault_id: str) -> bool:
    return store.library(config, vault_id) is not None


def expert_versions(
    config: Config, db: sqlite3.Connection, client: str, vault: str
) -> dict[str, tuple[str, str]]:
    """Current authorized registry metadata; this is not admission to an index."""
    from .organization import packages

    result = {
        p["id"]: (p["version"], p["path"]) for p in packages.listing(config, db, client, vault)
    }
    for row in db.execute(
        "SELECT p.task_id,p.artifact_sha256,"
        "COALESCE(l.entry_note_path,p.relative_path||'/note.md') "
        "FROM source_expert_publications p JOIN source_imports i ON i.id=p.task_id "
        "LEFT JOIN publication_locations l ON l.kind='expert' AND l.publication_id=p.task_id "
        "WHERE p.state='published' AND COALESCE(l.vault_id,i.vault_id)=?",
        (vault,),
    ):
        result[row[0]] = (row[1], row[2])
    return result


def capture_experts(
    config: Config, db: sqlite3.Connection, client: str, vault: str
) -> dict[str, tuple[str, str]]:
    """Index-time validation isolates one broken package and never invokes its runtime."""
    from . import published_expert
    from .vault_sync.explicit_refresh import package_issues

    result = {}
    failed = [issue["path"] for issue in package_issues(config, vault)]
    for identity, (version, path) in expert_versions(config, db, client, vault).items():
        if any(vault_scope.inside(path, base) for base in failed):
            continue
        try:
            module = published_expert.text(config, db, client, identity, version)
            if module["version"] == version and module["entry_note_path"] == path:
                result[identity] = (version, path)
        except (StorageError, OSError, ValueError, KeyError, TypeError, auth.Denied):
            continue
    return result


def publish_experts(index: sqlite3.Connection, values: dict[str, tuple[str, str]]) -> None:
    index.execute(EXPERT_SCHEMA)
    index.execute("DELETE FROM indexed_experts")
    index.executemany(
        "INSERT INTO indexed_experts VALUES(?,?,?)",
        [(identity, version, path) for identity, (version, path) in sorted(values.items())],
    )


def _experts(index: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    if not index.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='indexed_experts'"
    ).fetchone():
        return {}
    return {row[0]: (row[1], row[2]) for row in index.execute("SELECT * FROM indexed_experts")}


@dataclass(frozen=True)
class View:
    notes: dict[str, note_index.Note]
    experts: dict[str, tuple[str, str]]
    token: str
    coverage: dict[str, Any]
    fingerprint: str = ""


def read(config: Config, db: sqlite3.Connection, client: str | None, vault: sqlite3.Row) -> View:
    """Inspect only cached source paths; pending ancestors do not hide healthy siblings."""
    root = vaults.checked_vault(
        config, auth.grant(db, client, vault["id"], "query") if client else vault
    )
    scope = vault_scope.for_vault(root, vault)
    omitted: dict[str, str] = {}
    notes: dict[str, note_index.Note] = {}
    experts = {}
    token = "unavailable"
    try:
        with note_search_cache.connection(config, vault["id"]) as index:
            if index is None:
                raise ValueError("index absent")
            generation = index.execute("SELECT * FROM generation").fetchone()
            if generation is None or generation["state"] not in {"ready", "stale"}:
                raise ValueError("index unpublished")
            old_proof = visibility.previous_readonly(index)
            current = visibility.read(config, db, vault)
            layout = publication_locations.project_inventory(db, vault["id"])
            from .organization.packages import roots
            from .vault_merge_guard import retained_paths

            hidden = layout.hidden | retained_paths(db, vault["id"])
            hidden_roots = layout.hidden_roots | roots(config, vault["id"])
            observations = dict(index.execute("SELECT path,file_stat FROM observations"))
            changed = visibility.changed(old_proof, current, set(observations))
            for row in index.execute(
                "SELECT path,json_remove(metadata,'$.text') FROM notes ORDER BY path"
            ):
                path = row[0]
                # Scope is decided before touching any file, including protected prefixes.
                if (
                    not scope.allows(path)
                    or path in hidden
                    or any(vault_scope.inside(path, base) for base in hidden_roots)
                ):
                    continue
                if path in changed:
                    omitted[path] = "indexed_source_visibility_changed"
                    continue
                try:
                    old = json.loads(observations[path])
                    now = json.loads(signature((root / path).lstat()))
                    if now[8] & SF_DATALESS:
                        omitted[path] = "indexed_source_cloud_pending"
                        continue
                    # ctime alone is metadata drift; the current body is hash-checked before use.
                    if old[:6] + old[7:8] != now[:6] + now[7:8]:
                        omitted[path] = "indexed_source_changed"
                        continue
                    value = json.loads(row[1])
                    value["text"] = ""
                    value["file_key"] = tuple(value["file_key"]) if value["file_key"] else None
                    notes[path] = note_index.Note(**value)
                except (OSError, ValueError, KeyError, TypeError):
                    omitted[path] = "indexed_source_unavailable"
            from .vault_sync.explicit_refresh import package_issues

            failed = package_issues(config, vault["id"])
            current_experts = expert_versions(config, db, client, vault["id"]) if client else {}
            experts = {
                i: v
                for i, v in _experts(index).items()
                if current_experts.get(i) == v
                and not any(vault_scope.inside(v[1], issue["path"]) for issue in failed)
            }
            for identity, (_, path) in current_experts.items():
                if identity not in experts:
                    omitted[path] = "indexed_expert_pending"
            for issue in failed:
                omitted[issue["path"]] = issue["reason"]
            token = digest(
                canonical(
                    [generation["fingerprint"], current.token, sorted(notes), experts]
                ).encode()
            )
            with store.connection(config) as side:
                pending = (
                    side.execute(
                        "SELECT count(*) FROM pending WHERE vault=?", (vault["id"],)
                    ).fetchone()[0]
                    if side
                    else 0
                )
                issues = (
                    side.execute(
                        "SELECT count(*) FROM issues WHERE vault=?", (vault["id"],)
                    ).fetchone()[0]
                    if side
                    else 0
                )
            library = store.library(config, vault["id"])
            caught_up = (
                library is not None
                and library["indexed"] == library["generation"]
                and library["state"] == "ready"
                and not library["error"]
            )
            unavailable = len(omitted)
            return View(
                notes,
                experts,
                token,
                {
                    "scope": "indexed_sources",
                    "complete": not omitted and not pending and not issues and caught_up,
                    "unavailable_count": unavailable,
                    "unavailable": [{"path": p, "reason": r} for p, r in sorted(omitted.items())][
                        :100
                    ],
                    "pending": pending,
                    "needs_attention": issues + len(failed),
                    "package_issue_count": len(failed),
                },
                str(generation["fingerprint"]),
            )
    except (StorageError, OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return View(
            {},
            {},
            token,
            {
                "scope": "indexed_sources",
                "complete": False,
                "unavailable_count": 1,
                "unavailable": [{"reason": "index_unavailable"}],
            },
        )


def expert_allowed(config: Config, db: sqlite3.Connection, client: str, module: Any) -> bool:
    from .vault_sync.explicit_refresh import package_issues

    vid = module["vault_id"]
    if not managed(config, vid):
        return True
    identity = module["module_id"].removeprefix("expert:")
    auth.grant(db, client, vid, "query")
    expected = (module["version"], module["entry_note_path"])
    if any(vault_scope.inside(expected[1], issue["path"]) for issue in package_issues(config, vid)):
        return False
    try:
        with note_search_cache.connection(config, vid) as index:
            return (
                index is not None
                and index.execute("SELECT state FROM generation").fetchone()[0]
                in {"ready", "stale"}
                and _experts(index).get(identity) == expected
                and expert_versions(config, db, client, vid).get(identity) == expected
            )
    except (StorageError, OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return False
