"""Authorize, archive and publish complete captured articles; no network or model calls."""

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from . import (
    article_bindings,
    article_tasks,
    auth,
    database,
    note_index,
    note_migration_store,
    objects,
    publication_files,
    publication_origins,
    vaults,
)
from .article_capture import VerifiedCapture, read_bounded, verify_capture
from .config import Config, IntegrityError, InvalidInput, contained
from .execution_ledger import get_run
from .inputs import AssetInput, canonical
from .store import Receipt, Store


def content_files(capture: VerifiedCapture, *, date_evidence: bool = True) -> dict[str, bytes]:
    """Stable content excludes dynamic raw response bytes and per-run identities."""
    draft = capture.draft
    article = draft.article
    metadata = {
        "title": article.title,
        "author": article.author,
        "published": (article.date_evidence or {}).get("value", "")
        if date_evidence
        else article.published,
        "source": article.source_url,
    }
    note = (
        "---\n"
        + "\n".join(
            f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items()
        )
        + "\n---\n\n"
        + draft.markdown
    )
    return {
        "note.md": note.encode(),
        **{image.relative_path: image.data for image in draft.images},
        **(
            {"date-evidence.json": canonical(article.date_evidence).encode()}
            if date_evidence
            else {}
        ),
    }


def descriptors(files: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    return {
        name: {"sha256": objects.digest(data), "bytes": len(data)}
        for name, data in sorted(files.items())
    }


def archive(
    config: Config,
    db: sqlite3.Connection,
    run: sqlite3.Row,
    task: sqlite3.Row,
    capture: VerifiedCapture,
    manifest: bytes,
) -> Receipt:
    """Each submission keeps its raw response as a normal retrievable knowledge version."""
    vault = auth.grant(db, run["client_id"], run["vault_id"], "add")
    return Store(config).ingest_locked(
        db,
        ingress_scope="article-capture",
        event_id=run["id"],
        vault=vault["name"],
        source_key="article:" + objects.digest(task["source_url"].encode()),
        raw_event=canonical({"run_id": run["id"], "receipt_id": task["receipt_id"]}).encode(),
        items=(
            AssetInput(capture.raw_html, "source.html", "text/html", "body"),
            AssetInput(manifest, "capture.json", "application/json"),
            AssetInput(capture.draft.markdown.encode(), "extracted.md", "text/markdown"),
            *(
                AssetInput(image.data, Path(image.relative_path).name, image.media_type)
                for image in capture.draft.images
            ),
        ),
        session_scope="article-capture",
        session_id=run["id"],
        retention="knowledge",
        owner_client_id=run["client_id"],
    )


def bound(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    run_id: str,
) -> tuple[sqlite3.Row, sqlite3.Row, Path]:
    run = get_run(db, run_id)
    if run["client_id"] != client_id:
        raise auth.Denied(404)
    task = article_bindings.get(db, run_id)
    if task is None or task["state"] != "captured" or run["state"] != "succeeded":
        raise InvalidInput("only succeeded article captures may publish")
    article_bindings.authorize(config, db, run, task)
    vault = vaults.checked_vault(config, auth.grant(db, client_id, run["vault_id"], "add"))
    note_migration_store.guard(config, run["vault_id"])
    return run, task, vault


def publication_row(db: sqlite3.Connection, identity: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM article_publications WHERE id=?", (identity,)).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise IntegrityError("publication ledger missing")
    return row


def publish_locked(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    run_id: str,
    *,
    preview_only: bool = False,
) -> dict[str, Any]:
    """Caller holds database.locked across authorization, archive and filesystem commit."""
    run, task, vault = bound(config, db, client_id, run_id)
    previous = db.execute(
        "SELECT p.vault_id FROM article_publication_runs r "
        "JOIN article_publications p ON p.id=r.publication_id WHERE r.run_id=?",
        (run_id,),
    ).fetchone()
    if previous is not None and previous[0] != run["vault_id"]:
        raise IntegrityError("publication moved to another Vault; refuse original-run replay")
    output = article_tasks.output_path(config, task)
    manifest = read_bounded(output, "capture.json", 65536)
    if objects.digest(manifest) != task["manifest_sha256"]:
        raise IntegrityError("capture manifest changed")
    capture = verify_capture(output, task["source_url"])
    old = db.execute(
        "SELECT p.manifest_json FROM article_publication_runs r JOIN article_publications p "
        "ON p.id=r.publication_id WHERE r.run_id=?",
        (run_id,),
    ).fetchone()
    modern = old is None or "date-evidence.json" in json.loads(old[0])
    files = content_files(capture, date_evidence=modern)
    fingerprint = objects.digest(canonical(descriptors(files)).encode())
    from . import publication_layout

    planned_id = str(
        uuid5(
            NAMESPACE_URL,
            canonical(["fiber-article-layout", run["vault_id"], task["source_url"], fingerprint]),
        )
    )
    existing = db.execute(
        "SELECT id FROM article_publications WHERE vault_id=? AND source_url=? "
        "AND content_digest=?",
        (run["vault_id"], task["source_url"], fingerprint),
    ).fetchone()
    if existing:
        planned_id = existing[0]
    if preview_only:
        if not existing:
            publication_layout.prepare(
                config, db, "article", planned_id, run["vault_id"], files, refresh_preview=True
            )
        return publication_layout.preview_result(
            db, "article", planned_id, "article-" + planned_id, vault_id=run["vault_id"], root=vault
        )
    receipt = archive(config, db, run, task, capture, manifest)
    if receipt.state != "stored" or receipt.source_version_id is None:
        raise IntegrityError("article archive incomplete")
    with database.transaction(db):
        row = db.execute(
            "SELECT * FROM article_publications WHERE vault_id=? AND "
            "source_url=? AND content_digest=?",
            (run["vault_id"], task["source_url"], fingerprint),
        ).fetchone()
        created = row is None
        if row is None:
            identity = planned_id
            from .publication_layout import prepare

            prepare(config, db, "article", identity, run["vault_id"], files)
            db.execute(
                "INSERT INTO article_publications(id,vault_id,source_url,content_digest,"
                "version_id,relative_path,manifest_json,vault_dev,vault_ino,state) "
                "VALUES(?,?,?,?,?,?,?,?,?,'preparing')",
                (
                    identity,
                    run["vault_id"],
                    task["source_url"],
                    fingerprint,
                    receipt.source_version_id,
                    "article-" + identity,
                    canonical(descriptors(files)),
                    vault.stat().st_dev,
                    vault.stat().st_ino,
                ),
            )
            row = publication_row(db, identity)
        db.execute(
            "INSERT OR IGNORE INTO article_publication_runs VALUES(?,?,?)",
            (run_id, row["id"], receipt.job_id),
        )
        link = db.execute(
            "SELECT * FROM article_publication_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if link["publication_id"] != row["id"] or link["archive_receipt_id"] != receipt.job_id:
            raise IntegrityError("publication submission changed")
    if row["manifest_json"] != canonical(descriptors(files)):
        raise IntegrityError("publication content identity mismatch")
    # This marker binds recovery to the durable ledger; it is never trusted on its own.
    from . import publication_layout, publication_locations

    location = publication_locations.get(db, "article", row["id"])
    if location is None:
        files[".fiber-publication.json"] = canonical(
            {
                "publication_id": row["id"],
                "version_id": row["version_id"],
                "files": descriptors(files),
            }
        ).encode()
    try:
        layout_result = publication_layout.publish(
            config, db, "article", row["id"], client_id, files
        )
        if layout_result is None:
            finish(config, db, client_id, run_id, vault, row, files)
        else:
            db.execute(
                "UPDATE article_publications SET state='published',error_code=NULL WHERE id=?",
                (row["id"],),
            )
    except (OSError, ValueError, IntegrityError, InvalidInput):
        db.execute(
            "UPDATE article_publications SET error_code='publication_incomplete_or_changed' "
            "WHERE id=?",
            (row["id"],),
        )
        raise
    if created:
        publication_origins.save(
            config,
            row["id"],
            run_id,
            row["vault_id"],
            note_index.read_note(
                vault,
                (layout_result or {}).get("entry_note_path", row["relative_path"] + "/note.md"),
                1024 * 1024,
            ),
            row["manifest_json"],
        )
    return {
        "state": "published",
        "published": True,
        "publication_id": row["id"],
        "vault_id": run["vault_id"],
        "note_path": (layout_result or {}).get(
            "entry_note_path", row["relative_path"] + "/note.md"
        ),
        **(layout_result or {}),
        "source_version_id": row["version_id"],
        "archive_receipt_id": receipt.job_id,
        "archive_version_id": receipt.source_version_id,
    }


def check_vault_identity(vault: Path, expected: tuple[int, int]) -> None:
    info = vault.stat()
    if vault.is_symlink() or (info.st_dev, info.st_ino) != expected:
        raise IntegrityError("publication vault directory identity changed")


def finish(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    run_id: str,
    vault: Path,
    row: sqlite3.Row,
    files: dict[str, bytes],
) -> None:
    expected_identity = (row["vault_dev"], row["vault_ino"])
    check_vault_identity(vault, expected_identity)
    scope_row = auth.grant(db, client_id, row["vault_id"], "add")
    vaults.require_scope(config, scope_row, row["relative_path"])
    target = contained(vault, row["relative_path"])
    if row["state"] == "published":
        # Never recreate a deleted note, or overwrite human edits, even on explicit retry.
        publication_files.verify(vault, row["relative_path"], files)
    else:
        if row["state"] == "preparing":
            if target.exists():
                raise IntegrityError("publication destination already exists")
            staging = ".fiber-stage-" + str(uuid4())
            vaults.require_scope(config, scope_row, staging)
            db.execute(
                "UPDATE article_publications SET staging_path=? WHERE id=?", (staging, row["id"])
            )
            publication_files.stage(vault, staging, files)
            db.execute("UPDATE article_publications SET state='ready' WHERE id=?", (row["id"],))
            row = publication_row(db, row["id"])
        vaults.require_scope(config, scope_row, row["staging_path"])
        staging_path = contained(vault, row["staging_path"])
        if target.exists():
            if staging_path.exists():
                raise IntegrityError("both staging and destination exist; refuse adoption")
            publication_files.verify(vault, row["relative_path"], files)
        else:
            # If a rename happened then the destination was removed, missing staging
            # prevents automatic resurrection. Partial preparation uses a new directory.
            publication_files.verify(vault, row["staging_path"], files)
            _, _, current_vault = bound(config, db, client_id, run_id)
            if current_vault != vault:
                raise IntegrityError("publication vault changed")
            check_vault_identity(current_vault, expected_identity)
            publication_files.rename_exclusive(vault, row["staging_path"], row["relative_path"])
            publication_files.verify(vault, row["relative_path"], files)
    bound(config, db, client_id, run_id)
    check_vault_identity(vault, expected_identity)
    db.execute(
        "UPDATE article_publications SET state='published',error_code=NULL WHERE id=?", (row["id"],)
    )


def publish(config: Config, client_id: str, run_id: str) -> dict[str, Any]:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            return publish_locked(config, db, client_id, run_id)
        finally:
            db.close()
