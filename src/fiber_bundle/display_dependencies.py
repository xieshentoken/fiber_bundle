"""Semantic versions for the five audited, vault-scoped display projections.

Database file mtimes are deliberately absent: channel traffic and another vault's
publication may share the same SQLite file. Source reads still use request-local
file observations, and authentication is repeated by the delivery lease.
"""

import sqlite3
import time
from pathlib import Path
from typing import Any

from starlette.requests import Request

from . import auth, database, display_observation
from .config import Config, IntegrityError, contained
from .note_changes import identifier


def _rows(db: sqlite3.Connection, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    return sorted((tuple(row) for row in db.execute(sql, params)), key=repr)


def stamp(config: Config, request: Request) -> dict[str, Any]:
    vault = request.query_params.get("vault_id", "")
    if vault:
        identifier(vault)  # Before any directory or index path is constructed.
    client = request.query_params.get("client_id", "")
    endpoint = request.url.path
    index_status = endpoint == "/admin/note-index/status"
    organization = endpoint == "/admin/organization"
    result: dict[str, Any] = {}
    paths: list[Path] = []
    with database.connect(config.state_root, readonly=True) as db:
        auth.admin(db, request.cookies.get(auth.COOKIE, ""))
        row = db.execute("SELECT * FROM vaults WHERE id=?", (vault,)).fetchone()
        result["vault"] = tuple(row) if row else None
        result["client"] = _rows(db, "SELECT * FROM clients WHERE id=?", client)
        result["grant"] = _rows(
            db, "SELECT * FROM client_vault_grants WHERE client_id=? AND vault_id=?", client, vault
        )
        result["connection"] = _rows(
            db, "SELECT * FROM agent_connections WHERE client_id=?", client
        )
        if row is not None:
            from .vault_catalog import physical_path
            from .vault_scope import for_vault

            root = physical_path(config, row)
            for_vault(root, row)  # Reject a protected root before probing policy children.
            signature = display_observation.signature(root)
            result["root_identity"] = signature[:3] + signature[6:]
            paths.extend(
                parent / ".knowledge-organizer-policy.json" for parent in [*root.parents, root]
            )
        # These rows own visibility, inventory guards, save policy and MOC projection.
        # They are scoped by the requested vault, never the writer's original vault.
        for table, columns in (
            ("vault_save_policies", "vault_id,revision,state,updated_at"),
            ("automatic_policies", "vault_id,revision,updated_at"),
            (
                "publication_locations",
                "kind,publication_id,entry_note_path,package_root,revision,state",
            ),
            ("document_creation_saves", "run_id,intent_sha256,source_version_id,state"),
        ):
            result[table] = _rows(db, f"SELECT {columns} FROM {table} WHERE vault_id=?", vault)
        result["permission_epoch"] = _rows(
            db,
            "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
            client,
            vault,
        )
        result["operations"] = _rows(
            db,
            "SELECT id,kind,state,updated_at FROM vault_operations WHERE vault_id=? OR "
            "(kind='vault_merge' AND EXISTS (SELECT 1 FROM json_each(payload,"
            "'$.plan.selection.source_vault_ids') WHERE value=?))",
            vault,
            vault,
        )
        result["articles"] = _rows(
            db,
            "SELECT p.id,p.source_url,p.content_digest,p.vault_dev,p.vault_ino,p.version_id,"
            "p.relative_path,p.state FROM article_publications p LEFT JOIN publication_locations l "
            "ON l.kind='article' AND l.publication_id=p.id WHERE COALESCE(l.vault_id,p.vault_id)=?",
            vault,
        )
        for name, table, parent, key, kind, version in (
            (
                "documents",
                "document_publications",
                "execution_runs",
                "run_id",
                "document",
                "p.source_sha256,p.result_sha256,p.review_id,p.system_review_id",
            ),
            (
                "media",
                "media_publications",
                "media_tasks",
                "job_id",
                "media",
                "p.review_id,p.system_review_id",
            ),
            (
                "experts",
                "source_expert_publications",
                "source_imports",
                "task_id",
                "expert",
                "p.artifact_sha256",
            ),
        ):
            result[name] = _rows(
                db,
                f"SELECT p.{key},p.relative_path,p.state,{version} FROM {table} p "
                f"JOIN {parent} r ON r.id=p.{key} LEFT JOIN publication_locations l "
                f"ON l.kind='{kind}' AND l.publication_id=p.{key} "
                "WHERE COALESCE(l.vault_id,r.vault_id)=?",
                vault,
            )
        # Publication body/provenance binding fields live on their owning task rows.
        result["document_versions"] = _rows(
            db,
            "SELECT r.id,r.version_id,r.vault_id FROM execution_runs r "
            "JOIN document_publications p "
            "ON p.run_id=r.id LEFT JOIN publication_locations l ON l.kind='document' "
            "AND l.publication_id=p.run_id WHERE COALESCE(l.vault_id,r.vault_id)=?",
            vault,
        )
        result["media_bindings"] = _rows(
            db,
            "SELECT r.id,json_extract(r.binding_json,'$.source.version_id'),r.vault_id "
            "FROM media_tasks r JOIN media_publications p "
            "ON p.job_id=r.id LEFT JOIN publication_locations l ON l.kind='media' "
            "AND l.publication_id=p.job_id WHERE COALESCE(l.vault_id,r.vault_id)=?",
            vault,
        )
    from . import moc_store, note_identity_store, note_migration_store
    from .organization import store as ledger
    from .vault_sync import store as sync_store

    for name, connection, query in (
        (
            "identities",
            note_identity_store.connection,
            "SELECT * FROM registrations WHERE vault_id=?",
        ),
        ("members", moc_store.connection, "SELECT id,revision FROM mocs WHERE vault_id=?"),
    ):
        with connection(config) as db:
            result[name] = _rows(db, query, vault) if db else []
    with note_migration_store.connection(config) as db:
        result["migrations"] = (
            _rows(
                db,
                "SELECT id,source,target,phase FROM intents WHERE source=? OR target=?",
                vault,
                vault,
            )
            if db
            else []
        )
    selected = {"packages", "versions"}
    if organization:
        selected |= {
            "policies",
            "entities",
            "decisions",
            "model_reviews",
            "note_moc",
            "maintenance_undo",
            "manual_resolutions",
        }
    result["organization"] = {table: [] for table in sorted(selected)}
    result["intents"] = []
    with ledger.connection(config) as db:
        if db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            result["organization"].update(
                {
                    table: _rows(db, f"SELECT id,updated FROM {table} WHERE vault=?", vault)
                    for table in sorted(selected & tables)
                }
            )
            if organization and "jobs" in tables:
                result["intents"] = _rows(
                    db,
                    "SELECT id,json_extract(value,'$.generation','$.plan_sha256','$.confirmed') "
                    "FROM jobs WHERE vault=?",
                    vault,
                )
    result["observer"] = []
    if not index_status:
        result["observed_notes"] = []
    with sync_store.connection(config) as db:
        if db:
            columns = "id,binding,root,scope,baseline"
            if index_status:
                # Freshness can expire while a response is buffered. Heartbeat churn
                # within the available interval does not change this semantic version.
                columns += ",generation,indexed,state,error,(?-heartbeat<45)"
                result["observer"] = _rows(
                    db, f"SELECT {columns} FROM libraries WHERE id=?", time.time(), vault
                )
            else:
                result["observer"] = _rows(db, f"SELECT {columns} FROM libraries WHERE id=?", vault)
            if not index_status:
                result["observed_notes"] = _rows(
                    db,
                    "SELECT path,signature FROM files WHERE vault=? AND directory=0 "
                    "AND lower(path) LIKE '%.md'",
                    vault,
                )
    # Immutable MOC journals and the live index are partitioned by vault on disk.
    # Building copies and other vaults do not participate in this request.
    if row is not None:
        directory = contained(config.state_root, "moc-files/" + vault)
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_file():
                    paths.append(path)
                    if len(paths) > 20000:
                        raise IntegrityError("display_observation_limit")
    if row is not None:
        from .note_search_cache import location
        from .vault_sync.explicit_refresh import package_issues

        live = location(config, vault)
        paths.extend(live.with_name(live.name + suffix) for suffix in ("", "-wal", "-journal"))
        paths.append(live.with_suffix(".job.json"))
        paths.append(live.with_suffix(".refresh.json"))
        result["package_issues"] = package_issues(config, vault)
    if organization:
        paths.append(config.state_root / "knowledge-graph-policy.json")
    if len(paths) > 20000:
        raise IntegrityError("display_observation_limit")
    for path in paths:
        try:
            result[str(path)] = display_observation.signature(path)
        except FileNotFoundError:
            result[str(path)] = None
    return result
