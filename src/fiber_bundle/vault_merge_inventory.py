"""Read-only complete publication/source inventory for a bounded, explicitly scoped merge."""

import json
from dataclasses import asdict
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import (
    auth,
    moc_file_journal,
    moc_store,
    note_inventory,
    publication_file_io,
    publication_locations,
    vault_directories,
    vault_files,
)
from .attachment_projection import epoch
from .config import Config, InvalidInput
from .objects import digest, read_object

MAX_FILES = 4096
MAX_BYTES = 512 * 1024 * 1024


def context(config: Config, db: Any, client: str, vault: str) -> dict[str, Any]:
    for permission in ("query", "original", "add"):
        auth.grant(db, client, vault, permission)
    root, scope, row = vault_directories.vault_root(config, db, vault)
    if row["read_only"]:
        raise InvalidInput("vault_merge_requires_writable_sources_and_target")
    return {
        "vault_id": vault,
        "root": str(root),
        "root_key": vault_directories.key(root),
        "scope": scope.wire(),
        "permission_epoch": epoch(db, client, vault),
        "row": {
            k: row[k]
            for k in ("status", "attachment_state", "scope_json", "read_only", "default_query")
        },
        "grants": [
            dict(r)
            for r in db.execute(
                "SELECT * FROM client_vault_grants WHERE vault_id=? ORDER BY client_id", (vault,)
            )
        ],
    }


def mocs(config: Config, vault: str) -> list[dict[str, Any]]:
    with moc_store.connection(config) as side:
        ids = (
            []
            if side is None
            else [
                r[0]
                for r in side.execute("SELECT id FROM mocs WHERE vault_id=? ORDER BY id", (vault,))
            ]
        )
    result = []
    for identity in ids:
        record = moc_store.load(config, vault, identity)
        assert record is not None
        plans = moc_file_journal.plans(config, vault, identity)
        if any(p.status == "pending" for p in plans):
            raise InvalidInput("vault_merge_unresolved_moc_write")
        current = moc_file_journal.latest(plans)
        result.append(
            {
                "id": identity,
                "record": asdict(record),
                "file_path": current.value["path"] if current else None,
                "file_audit": current.plan_id if current else None,
            }
        )
    return result


def describe(config: Config, db: Any, client: str, vault: str) -> dict[str, Any]:
    before = context(config, db, client, vault)
    root, scope, row = vault_directories.vault_root(config, db, vault)
    inventory = note_inventory.read(config, db, row)
    files = {f["path"]: f for f in vault_files.inventory(root, scope)["files"]}
    publications = []
    covered = set()
    for raw in db.execute("SELECT * FROM publication_locations WHERE vault_id=?", (vault,)):
        location = publication_locations.get(db, raw["kind"], raw["publication_id"])
        assert location is not None
        if not publication_locations.committed(db, location):
            raise InvalidInput("vault_merge_unfinished_publication")
        plan = location["plan"]
        paths = set(plan["mapping"].values())
        publications.append(
            {
                "kind": raw["kind"],
                "id": raw["publication_id"],
                "entry": raw["entry_note_path"],
                "package": raw["package_root"],
                "mapping": plan["mapping"],
                "body_files": plan.get("body_files", ["note.md"]),
                "supplement_roles": plan.get("supplement_roles", {}),
                "location_revision": raw["revision"],
                "location": dict(raw),
                "original_hashes": {
                    logical: plan["published_hashes"][path]
                    for logical, path in plan["mapping"].items()
                },
            }
        )
        covered.update(paths)
    # Older bundle ledgers have no publication_locations row. Preserve their whole verified tree.
    queries = {
        "article": (
            "SELECT id AS pid,relative_path,manifest_json FROM article_publications "
            "WHERE vault_id=? "
            "AND state='published'",
        ),
        "document": (
            "SELECT p.run_id AS pid,p.relative_path,p.manifest_json FROM "
            "document_publications p JOIN "
            "execution_runs r ON r.id=p.run_id WHERE r.vault_id=? AND p.state='published'",
        ),
        "media": (
            "SELECT p.job_id AS pid,p.relative_path,p.manifest_json FROM "
            "media_publications p JOIN media_tasks m "
            "ON m.id=p.job_id WHERE m.vault_id=? AND p.state='published'",
        ),
        "expert": (
            "SELECT p.task_id AS pid,p.relative_path,p.manifest_json FROM "
            "source_expert_publications p JOIN "
            "source_imports i ON i.id=p.task_id WHERE i.vault_id=? AND p.state='published'",
        ),
    }
    for kind, (sql,) in queries.items():
        for pub in db.execute(sql, (vault,)).fetchall():
            if publication_locations.get(db, kind, pub["pid"]):
                continue
            tree = publication_file_io.tree(root, pub["relative_path"], scope)
            mapping = {logical: pub["relative_path"] + "/" + logical for logical in tree}
            manifest = json.loads(pub["manifest_json"])
            expected_files = {
                name: entry
                for name, entry in manifest.get("files", manifest).items()
                if isinstance(entry, dict) and "sha256" in entry
            }
            if set(expected_files) - set(tree):
                raise InvalidInput("vault_merge_managed_file_missing")
            publications.append(
                {
                    "kind": kind,
                    "id": pub["pid"],
                    "entry": mapping["note.md"],
                    "package": pub["relative_path"],
                    "mapping": mapping,
                    "body_files": [
                        n
                        for n in tree
                        if n.endswith(".md") and not any(p.startswith(".") for p in n.split("/"))
                    ],
                    "location_revision": 0,
                    "location": None,
                    "original_hashes": {
                        logical: entry["sha256"] for logical, entry in expected_files.items()
                    },
                }
            )
            covered.update(mapping.values())
    from .vault_merge_analysis import records as analysis_records

    analyses = analysis_records(db, vault)
    for analysis in analyses:
        covered.update(analysis["paths"])
    from .vault_merge_intake import publications as intake_publications

    for pub in intake_publications(config, db, vault, root, scope):
        publications.append(pub)
        covered.update(pub["mapping"].values())
    for path in covered:
        if path not in files:
            raw, key = publication_file_io.read(root, path)
            files[path] = {
                "path": path,
                "bytes": len(raw),
                "sha256": digest(raw),
                "dev": key[0],
                "ino": key[1],
                "mtime_ns": (root / path).stat().st_mtime_ns,
            }
    if len(files) > MAX_FILES or sum(f["bytes"] for f in files.values()) > MAX_BYTES:
        raise InvalidInput("vault_merge_inventory_limit")
    notes = []
    for note in inventory.notes:
        identity = note.identity or (
            "publication:" + note.publication_id
            if note.publication_id
            else "note:"
            + str(
                uuid5(
                    NAMESPACE_URL,
                    f"fiber-merge-history:{vault}:{note.relative_path}:{note.file_key}",
                )
            )
        )
        notes.append(
            {
                "path": note.relative_path,
                "identity": identity,
                "file_key": note.file_key,
                "sha256": note.sha256,
                "title": note.title,
                "publication_id": note.publication_id,
                "managed": note.relative_path in covered,
                "analysis": next(
                    (
                        {
                            **a["intent"],
                            "user_revision": a["intent"].get("user_revision", False)
                            or note.sha256 != a["intent"]["files"]["note.md"]["sha256"],
                        }
                        for a in reversed(analyses)
                        if a["note_path"] == note.relative_path
                    ),
                    None,
                ),
            }
        )
    referenced = set()
    for pub in publications:
        if pub["kind"] == "document":
            referenced.add(
                db.execute(
                    "SELECT version_id FROM execution_runs WHERE id=?", (pub["id"],)
                ).fetchone()[0]
            )
        elif pub["kind"] == "media":
            referenced.add(
                json.loads(
                    db.execute(
                        "SELECT binding_json FROM media_tasks WHERE id=?", (pub["id"],)
                    ).fetchone()[0]
                )["source"]["version_id"]
            )
        elif pub["kind"] == "expert":
            referenced.add(
                db.execute(
                    (
                        "SELECT r.source_version_id FROM receipts r JOIN source_imports i ON "
                        "i.receipt_id=r.job_id WHERE i.id=?"
                    ),
                    (pub["id"],),
                ).fetchone()[0]
            )
        elif pub["kind"] == "intake":
            source_binding = pub.get("intake_source") or json.loads(pub["location"]["value"]).get(
                "intake_source"
            )
            if source_binding:
                referenced.add(source_binding["source_version_id"])
    sources = []
    for version in db.execute(
        "SELECT v.id,v.retention FROM source_versions v JOIN sources s ON s.id=v.source_id "
        "WHERE s.vault_id=? ORDER BY v.id",
        (vault,),
    ):
        if version["retention"] != "knowledge" and version["id"] not in referenced:
            continue
        assets = [
            dict(r)
            for r in db.execute(
                "SELECT va.*,a.sha256,a.byte_count FROM version_assets va JOIN assets a "
                "ON a.id=va.asset_id WHERE va.version_id=? ORDER BY va.ordinal",
                (version[0],),
            )
        ]
        if (
            not assets
            or not db.execute(
                "SELECT 1 FROM receipts WHERE source_version_id=? AND state='stored'", (version[0],)
            ).fetchone()
        ):
            raise InvalidInput("vault_merge_original_asset_incomplete")
        receipt = db.execute(
            (
                "SELECT r.raw_asset_id,a.* FROM receipts r JOIN assets a ON "
                "a.id=r.raw_asset_id WHERE r.source_version_id=? AND r.state='stored' ORDER "
                "BY r.job_id LIMIT 1"
            ),
            (version[0],),
        ).fetchone()
        if receipt is None:
            raise InvalidInput("vault_merge_raw_event_missing")
        for asset in [*assets, dict(receipt)]:
            if "relative_path" not in asset:
                asset["relative_path"] = db.execute(
                    "SELECT relative_path FROM assets WHERE id=?", (asset["asset_id"],)
                ).fetchone()[0]
            read_object(
                config.state_root, asset["relative_path"], asset["sha256"], asset["byte_count"]
            )
        sources.append({"version_id": version[0], "assets": assets, "raw_event": dict(receipt)})
    after = context(config, db, client, vault)
    if before != after:
        raise InvalidInput("vault_merge_scope_changed")
    return {
        "analyses": analyses,
        "context": before,
        "files": list(sorted(files.values(), key=lambda f: f["path"])),
        "notes": notes,
        "publications": publications,
        "mocs": mocs(config, vault),
        "sources": sources,
    }
