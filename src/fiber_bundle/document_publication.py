"""Explicit human-reviewed document publication with durable no-replace recovery.

Caller holds the storage lock and authenticates the local administrator. Sources,
review decisions and immutable manifests remain bound; failures never overwrite notes.
"""

import json
import sqlite3
import time
from typing import Any

from . import auth, document_evidence, document_review, publication_files, vaults
from .config import Config, IntegrityError, InvalidInput, contained
from .inputs import canonical
from .objects import digest


def files(
    metadata: dict[str, Any], candidate: dict[str, Any], review: sqlite3.Row | dict[str, Any]
) -> dict[str, bytes]:
    """Compatibility rendering for callers of the original single-note helper."""
    from .document_rendering import render

    result, _ = render(metadata, candidate, review)
    return result


def publish_locked(
    config: Config,
    db: sqlite3.Connection,
    run_id: str,
    *,
    preview_only: bool = False,
    system_review_id: str | None = None,
) -> dict[str, Any]:
    run = db.execute("SELECT * FROM execution_runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise auth.Denied(404)
    metadata, candidate = document_evidence.load(config, db, run["client_id"], run_id)
    vault = vaults.checked_vault(config, auth.grant(db, run["client_id"], run["vault_id"], "add"))
    review: sqlite3.Row | dict[str, Any]
    if system_review_id is not None:
        from .automatic_review import credential

        human = document_review.latest(db, run_id)
        if (
            human is not None
            and not document_review.gate(candidate, json.loads(human["assessment_json"]))[
                "publication_ready"
            ]
        ):
            raise InvalidInput("human_review_blocks_automatic_publication")
        review = credential(
            config,
            db,
            system_review_id,
            "document",
            run_id,
            candidate["source_sha256"],
            candidate["result_sha256"],
        )
    else:
        human = document_review.latest(db, run_id)
        if human is None:
            raise InvalidInput("document not reviewed")
        review = human
        document_review.require_publication(candidate, json.loads(review["assessment_json"]))
    row = db.execute("SELECT * FROM document_publications WHERE run_id=?", (run_id,)).fetchone()
    from . import publication_layout, publication_locations

    if row is not None and row["state"] == "published":
        # Completion is historical: never recreate manually edited/moved/deleted files.
        return {
            "run_id": run_id,
            "state": "published",
            "relative_path": row["relative_path"],
            "historical": True,
            **publication_locations.wire(db, "document", run_id),
            "knowledge_verified": False,
        }
    from .document_rendering import render

    intended, source_units = render(metadata, candidate, review)
    if metadata.get("import_references"):
        from .source_import_references import files as reference_files
        from .source_import_references import rewrite

        attachments, names = reference_files(config, metadata["import_references"])
        intended.update(attachments)
        intended["note.md"] = rewrite(intended["note.md"].decode(), names).encode()
        # Markdown import is a single literal source unit; offsets follow controlled link rewrites.
        for unit in source_units:
            unit.update(rendered_start=0, rendered_end=len(intended["note.md"]))
        provenance = json.loads(intended["provenance.json"])
        provenance["import_references"] = metadata["import_references"]
        intended["provenance.json"] = canonical(provenance).encode()
    from .source_processing_contract import check_markdown
    from .source_task_policy import frozen

    plan = frozen(db, run_id)
    from . import document_parts, document_split

    limits = (
        plan["limits"]
        if plan is not None
        else {"markdown_bytes": 1024 * 1024, "markdown_total_bytes": 1024 * 1024}
    )
    if plan is None or plan["policy"]["oversize_action"] == "pause":
        if plan is not None:
            check_markdown(plan, intended)
        elif len(intended["note.md"]) > 1024 * 1024:
            raise InvalidInput("published document exceeds searchable note limit")
    else:
        try:
            intended = document_parts.publish_files(
                intended,
                metadata,
                review["id"],
                source_units,
                limits["markdown_bytes"],
                limits["markdown_total_bytes"],
            )
        except document_split.SplitRequired as exc:
            db.execute("UPDATE document_tasks SET error_code=? WHERE run_id=?", (str(exc), run_id))
            raise
    if plan is not None:
        check_markdown(plan, intended)
    manifest = canonical(
        {
            "vault_key": [vault.stat().st_dev, vault.stat().st_ino],
            **{
                n: {"sha256": digest(data), "byte_count": len(data)} for n, data in intended.items()
            },
        }
    )
    row = db.execute("SELECT * FROM document_publications WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        publication_layout.prepare(
            config,
            db,
            "document",
            run_id,
            run["vault_id"],
            intended,
            refresh_preview=preview_only,
            source_units=source_units,
            markdown_limits=limits,
        )
        if preview_only:
            return publication_layout.preview_result(
                db, "document", run_id, "Document-" + run_id, vault_id=run["vault_id"], root=vault
            )

        relative = "Document-" + run_id
        staging = ".fiber-document-" + run_id
        db.execute(
            "INSERT INTO document_publications(run_id,review_id,source_sha256,result_sha256,"
            "relative_path,staging_path,manifest_json,state,created_at,system_review_id) "
            "VALUES(?,?,?,?,?,?,?,'prepared',?,?)",
            (
                run_id,
                None if system_review_id else review["id"],
                metadata["source_sha256"],
                metadata["result_sha256"],
                relative,
                staging,
                manifest,
                time.time(),
                system_review_id,
            ),
        )
        row = db.execute("SELECT * FROM document_publications WHERE run_id=?", (run_id,)).fetchone()
    if (
        row["system_review_id"] or row["review_id"],
        row["source_sha256"],
        row["result_sha256"],
        row["manifest_json"],
    ) != (
        review["id"],
        metadata["source_sha256"],
        metadata["result_sha256"],
        manifest,
    ):
        raise IntegrityError("document publication binding changed")
    if row["state"] == "conflict":
        raise IntegrityError("document publication conflict requires reconciliation")
    if preview_only:
        return publication_layout.preview_result(
            db, "document", run_id, row["relative_path"], vault_id=run["vault_id"], root=vault
        )
    layout_result = publication_layout.publish(
        config, db, "document", run_id, run["client_id"], intended
    )
    if layout_result is not None:
        db.execute("UPDATE document_publications SET state='published' WHERE run_id=?", (run_id,))
        from .note_search_jobs import record

        record(config, run["vault_id"], "pending", reason="document_publication")
        return {
            "state": "published",
            "relative_path": row["relative_path"],
            "knowledge_verified": False,
            **layout_result,
            "run_id": run_id,
        }

    stage, target = row["staging_path"], row["relative_path"]
    scope_row = auth.grant(db, run["client_id"], run["vault_id"], "add")
    vaults.require_scope(config, scope_row, target)
    vaults.require_scope(config, scope_row, stage)
    if row["state"] == "prepared":
        if contained(vault, target).exists() or contained(vault, stage).exists():
            db.execute(
                "UPDATE document_publications SET state='conflict' WHERE run_id=?", (run_id,)
            )
            raise IntegrityError("document publication path already exists")
        db.execute("UPDATE document_publications SET state='publishing' WHERE run_id=?", (run_id,))
        publication_files.stage(vault, stage, intended)
    if contained(vault, target).exists():
        if contained(vault, stage).exists():
            raise IntegrityError("document publication ambiguous effects")
        publication_files.verify(vault, target, intended)
    else:
        publication_files.verify(vault, stage, intended)
        publication_files.rename_exclusive(vault, stage, target)
        publication_files.verify(vault, target, intended)
    if [vault.stat().st_dev, vault.stat().st_ino] != json.loads(row["manifest_json"])["vault_key"]:
        raise IntegrityError("document publication vault identity changed")
    db.execute("UPDATE document_publications SET state='published' WHERE run_id=?", (run_id,))
    from .note_search_jobs import record

    record(config, metadata["vault_id"], "pending", reason="document_publication")
    return {
        "run_id": run_id,
        "state": "published",
        "relative_path": target,
        "knowledge_verified": False,
    }
