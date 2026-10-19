"""Explicit human-reviewed media publication using exclusive durable file primitives."""

import base64
import hashlib
import json
import re
import sqlite3
import time
from typing import Any

from . import auth, media_jobs, media_processing, media_review, publication_files, vaults
from .config import Config, IntegrityError, InvalidInput, contained
from .media_files import canonical


def files(
    value: dict[str, Any],
    bound: dict[str, Any],
    review: sqlite3.Row | dict[str, Any],
    origin: dict[str, Any] | None = None,
) -> dict[str, bytes]:
    system = isinstance(review, dict) and review.get("system_review", False)
    source = bound["source"]
    audio_only = bound["request"]["evidence_mode"] == "audio_only"
    lines = [
        "---",
        'title: "Reviewed media"',
        "source_kind: published_media",
        "source_sha256: " + value["source_sha256"],
        "source_version_id: " + source["version_id"],
        "source_asset_id: " + source["asset_id"],
        "review_id: " + review["id"],
        "knowledge_verified: false",
        "---",
        "# Reviewed media",
        "",
        "音频优先整理，未核验画面内容"
        if audio_only
        else "System structural checks only; audio and visual content not human reviewed."
        if system
        else "Audio and visual source reviewed by administrator.",
        "",
        "Independent verification has not been performed.",
        "",
        f"Original: /v1/vaults/{source['vault_id']}/versions/"
        f"{source['version_id']}/assets/{source['asset_id']}",
        "",
    ]
    if origin is not None:
        lines += [
            "Platform source: " + origin["url"],
            "",
            "Acquisition: " + origin["access_state"],
            "",
        ]
    for c in value["transcript"]["cues"]:
        fence = "`" * max(
            3, max((len(m.group()) + 1 for m in re.finditer("`+", c["text"])), default=3)
        )
        lines += [f"## {c['start_ms']}–{c['end_ms']} ms", "", fence + "text", c["text"], fence, ""]
    out = {}
    for f in value["frames"]:
        name = "assets/" + f["name"]
        out[name] = base64.b64decode(f["data"], validate=True)
        lines += [f"Frame at {f['timestamp_ms']} ms: ![]({name})", ""]
    out["note.md"] = "\n".join(lines).encode()
    if len(out["note.md"]) > 1024 * 1024:
        raise InvalidInput("media_publication_note_limit")
    out["provenance.json"] = canonical(
        {
            "schema": "fiber-media-publication/v1",
            "job_id": value["job_id"],
            "source": source,
            "platform_origin": origin,
            "result_sha256": value["result_sha256"],
            "glossary_revision": value["glossary_revision"],
            "glossary_sha256": value["glossary_sha256"],
            "transcript": value["transcript"],
            "review_id": review["id"],
            "assessment": json.loads(review["assessment_json"]),
            "knowledge_verified": False,
        }
    )
    return out


def publish_locked(
    config: Config,
    db: sqlite3.Connection,
    job_id: str,
    *,
    review_id: str | None = None,
    system_review_id: str | None = None,
    preview_only: bool = False,
) -> dict[str, Any]:
    job = db.execute("SELECT * FROM media_tasks WHERE id=?", (job_id,)).fetchone()
    if job is None:
        raise auth.Denied(404)
    value = media_processing.result_locked(config, db, job["client_id"], job_id)
    vault = vaults.checked_vault(config, auth.grant(db, job["client_id"], job["vault_id"], "add"))
    human = media_review.latest(db, job_id)
    review: sqlite3.Row | dict[str, Any] | None = human
    if system_review_id:
        from .automatic_review import credential

        review = credential(
            config,
            db,
            system_review_id,
            "media",
            job_id,
            value["source_sha256"],
            value["result_sha256"],
        )
        if (
            human
            and not media_review.gate(value, json.loads(human["assessment_json"]))[
                "publication_ready"
            ]
        ):
            raise InvalidInput("human_review_blocks_automatic_publication")
    else:
        if review_id is not None and (human is None or human["id"] != review_id):
            raise InvalidInput("media_publication_review_changed")
        if (
            human is None
            or not media_review.gate(value, json.loads(human["assessment_json"]))[
                "publication_ready"
            ]
        ):
            raise InvalidInput("media_publication_review_required")
    assert review is not None
    row = db.execute("SELECT * FROM media_publications WHERE job_id=?", (job_id,)).fetchone()
    from . import publication_layout, publication_locations

    if row is not None and row["state"] == "published":
        return {
            "job_id": job_id,
            "state": "published",
            "relative_path": row["relative_path"],
            "historical": True,
            **publication_locations.wire(db, "media", job_id),
            "knowledge_verified": False,
        }
    link = db.execute(
        "SELECT * FROM media_link_tasks WHERE job_id=? AND state='complete'", (job_id,)
    ).fetchone()
    origin = None
    if link is not None:
        from .media_link_contract import source_url

        if link["client_id"] != job["client_id"] or link["vault_id"] != job["vault_id"]:
            raise IntegrityError("media_publication_origin_changed")
        origin = {
            "url": source_url(link["url"]),
            "access_state": json.loads(link["binding_json"])["capture"]["access_state"],
        }
    intended = files(value, media_jobs.binding(job), review, origin)
    manifest = canonical(
        {
            "vault_key": [vault.stat().st_dev, vault.stat().st_ino],
            **{
                n: {"sha256": hashlib.sha256(b).hexdigest(), "byte_count": len(b)}
                for n, b in intended.items()
            },
        }
    ).decode()
    if row is None:
        publication_layout.prepare(
            config, db, "media", job_id, job["vault_id"], intended, refresh_preview=preview_only
        )
        if preview_only:
            return publication_layout.preview_result(
                db, "media", job_id, "Media-" + job_id, vault_id=job["vault_id"], root=vault
            )

        target = "Media-" + job_id
        stage = ".fiber-media-" + job_id
        db.execute(
            "INSERT INTO media_publications VALUES(?,?,?,?,?,'prepared',?,?)",
            (
                job_id,
                None if system_review_id else review["id"],
                target,
                stage,
                manifest,
                time.time(),
                system_review_id,
            ),
        )
        row = db.execute("SELECT * FROM media_publications WHERE job_id=?", (job_id,)).fetchone()
    if (row["system_review_id"] or row["review_id"]) != review["id"] or row[
        "manifest_json"
    ] != manifest:
        raise IntegrityError("media_publication_binding_changed")
    if row["state"] == "conflict":
        raise IntegrityError("media_publication_conflict")
    if preview_only:
        return publication_layout.preview_result(
            db, "media", job_id, row["relative_path"], vault_id=job["vault_id"], root=vault
        )
    layout_result = publication_layout.publish(
        config, db, "media", job_id, job["client_id"], intended
    )
    if layout_result is not None:
        db.execute("UPDATE media_publications SET state='published' WHERE job_id=?", (job_id,))
        from .note_search_jobs import record

        record(config, job["vault_id"], "pending", reason="document_publication")
        return {
            "state": "published",
            "relative_path": row["relative_path"],
            "knowledge_verified": False,
            **layout_result,
            "job_id": job_id,
        }

    target, stage = row["relative_path"], row["staging_path"]
    scope_row = auth.grant(db, job["client_id"], job["vault_id"], "add")
    vaults.require_scope(config, scope_row, target)
    vaults.require_scope(config, scope_row, stage)
    if row["state"] == "prepared":
        if contained(vault, target).exists() or contained(vault, stage).exists():
            db.execute("UPDATE media_publications SET state='conflict' WHERE job_id=?", (job_id,))
            raise IntegrityError("media_publication_path_exists")
        db.execute("UPDATE media_publications SET state='publishing' WHERE job_id=?", (job_id,))
        publication_files.stage(vault, stage, intended)
    if contained(vault, target).exists():
        if contained(vault, stage).exists():
            raise IntegrityError("media_publication_ambiguous_effects")
        publication_files.verify(vault, target, intended)
    else:
        publication_files.verify(vault, stage, intended)
        publication_files.rename_exclusive(vault, stage, target)
        publication_files.verify(vault, target, intended)
    if [vault.stat().st_dev, vault.stat().st_ino] != json.loads(row["manifest_json"])["vault_key"]:
        raise IntegrityError("media_publication_vault_changed")
    db.execute("UPDATE media_publications SET state='published' WHERE job_id=?", (job_id,))
    return {
        "job_id": job_id,
        "state": "published",
        "relative_path": target,
        "knowledge_verified": False,
    }
