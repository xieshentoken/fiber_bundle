"""Current bounded Vault notes and publication bindings; caller authorizes and holds lock."""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import (
    note_identities,
    note_identity_store,
    note_index,
    note_migration_store,
    note_publications,
    vaults,
)
from .config import Config, IntegrityError


@dataclass(frozen=True)
class Inventory:
    root: Path
    notes: list[note_index.Note]
    publications: dict[str, sqlite3.Row]
    bindings: dict[str, note_publications.Binding]
    locations: dict[str, str]


def read(
    config: Config,
    db: sqlite3.Connection,
    vault: sqlite3.Row,
    *,
    registered: bool = True,
    projection: tuple[note_identity_store.Registration, note_identity_store.Registration]
    | None = None,
    limits: note_index.Limits | None = None,
    on_note: Callable[[note_index.Note], None] | None = None,
    note_loader: Callable[[str, int], note_index.Note] | None = None,
    exclude_path: Callable[[str], bool] | None = None,
) -> Inventory:
    from .note_placement import guard

    guard(db, vault["id"])
    note_migration_store.guard(config, vault["id"])
    root = vaults.checked_vault(config, vault)
    rows = db.execute(
        "SELECT p.* FROM article_publications p LEFT JOIN publication_locations l ON "
        "l.kind='article' AND l.publication_id=p.id WHERE COALESCE(l.vault_id,p.vault_id)=?",
        (vault["id"],),
    ).fetchall()
    from . import publication_locations

    managed, hidden = publication_locations.inventory(db, vault["id"])
    mapped_articles = {
        r[0]
        for r in db.execute(
            "SELECT publication_id FROM publication_locations WHERE kind='article' AND vault_id=?",
            (vault["id"],),
        )
    }
    published = {
        p["relative_path"]: p
        for p in rows
        if p["state"] == "published" and p["id"] not in mapped_articles
    }
    pending_documents = {
        p[0]
        for p in db.execute(
            "SELECT p.relative_path FROM document_publications p "
            "JOIN execution_runs r ON r.id=p.run_id "
            "WHERE r.vault_id=? AND p.state!='published'",
            (vault["id"],),
        )
    }
    pending_documents.update(
        p[0]
        for p in db.execute(
            "SELECT p.relative_path FROM media_publications p JOIN media_tasks m ON m.id=p.job_id "
            "WHERE m.vault_id=? AND p.state!='published'",
            (vault["id"],),
        )
    )
    pending_documents.update(
        p[0]
        for p in db.execute(
            "SELECT p.relative_path FROM source_expert_publications p "
            "JOIN source_imports i ON i.id=p.task_id WHERE i.vault_id=? AND p.state!='published'",
            (vault["id"],),
        )
    )
    from .document_creation_save import inventory_paths

    analysis_paths, analysis_published = inventory_paths(db, vault["id"])
    identities = {p["id"]: p for p in rows if p["state"] == "published"}
    bindings = {pid: publication_locations.binding(db, p) for pid, p in identities.items()}
    info = root.stat()
    from .vault_merge_guard import retained_paths

    retained = retained_paths(db, vault["id"])
    records = (
        [
            r
            for r in note_identity_store.records(config, vault["id"])
            if r.relative_path not in retained
        ]
        if registered
        else []
    )
    if projection is not None:
        before, after = projection
        if (
            not registered
            or before.identity != after.identity
            or before.publication_id is not None
            or after.publication_id is not None
            or before not in records
        ):
            raise IntegrityError("MOC projection identity conflict")
        records = [after if r == before else r for r in records]
    tracked = {}
    for record in records:
        note_identities.validate(record)
        if record.vault_key != (info.st_dev, info.st_ino):
            raise IntegrityError("note identity vault changed")
        if record.publication_id is not None:
            if record.publication_id not in bindings:
                raise IntegrityError("tracked publication is no longer published")
            tracked[record.publication_id] = record.file_key
    for publication in published.values():
        if (info.st_dev, info.st_ino) != (publication["vault_dev"], publication["vault_ino"]):
            raise IntegrityError("published vault identity changed")
    from . import document_parts

    parts = document_parts.bindings(db, vault["id"], root)
    locations: dict[str, str] = {}
    from .attachment_projection import hidden_roots as attachment_roots
    from .organization.packages import roots as expert_roots
    from .vault_scope import for_vault

    notes = note_index.snapshot(
        root,
        set(published) | analysis_published,
        {p["relative_path"] for p in rows} | pending_documents | analysis_paths,
        publications=bindings,
        tracked_publications=tracked,
        located_paths=locations,
        plain_published=analysis_published,
        limits=limits,
        on_note=on_note,
        note_loader=note_loader,
        exclude_path=exclude_path,
        scope=for_vault(root, vault),
        managed_entries=managed,
        hidden_files=hidden | retained,
        hidden_roots=expert_roots(config, vault["id"])
        | attachment_roots(db, vault["id"])
        | {
            json.loads(r[0])["retained_source_root"]
            for r in db.execute(
                "SELECT value FROM publication_locations WHERE vault_id=?", (vault["id"],)
            )
            if "retained_source_root" in json.loads(r[0])
        }
        | {
            r["package_root"]
            for r in db.execute(
                "SELECT * FROM publication_locations WHERE vault_id=?", (vault["id"],)
            )
            if r["package_root"] and not publication_locations.committed(db, dict(r))
        },
    )
    notes = [document_parts.annotate(n, parts) for n in notes]
    notes = note_identities.resolve(notes, records, (info.st_dev, info.st_ino))
    if projection is not None:
        before, after = projection
        if any(n.file_key == before.file_key for n in notes) or not any(
            (n.relative_path, n.sha256, n.file_key, n.identity)
            == (after.relative_path, after.sha256, after.file_key, after.identity)
            for n in notes
        ):
            raise IntegrityError("MOC projection file evidence changed")
    return Inventory(root, notes, identities, bindings, locations)
