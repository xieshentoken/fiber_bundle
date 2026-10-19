"""Explicit hash/inode-bound linked-media cleanup after reviewed publication; resumable audit."""

import hashlib
import json
import os
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any

from . import auth, media_jobs, media_processing, media_processing_worker, publication_files, vaults
from .config import Config, IntegrityError, InvalidInput, contained, sync_directory
from .media_files import canonical
from .media_link_worker import directory as link_directory
from .media_processing_capture import candidate
from .media_processing_files import read
from .media_worker import directory as parent_directory


def info(path: Path, sha: str) -> dict[str, Any]:
    if path.absolute() != path.resolve() or path.is_symlink():
        raise InvalidInput("media_cleanup_redirect")
    a = path.lstat()
    if not stat.S_ISREG(a.st_mode) or a.st_nlink != 1 or not 0 < a.st_size <= 8 * 1024 * 1024:
        raise InvalidInput("media_cleanup_file_type")
    data = path.read_bytes()
    b = path.lstat()
    if (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns) != (
        b.st_dev,
        b.st_ino,
        b.st_size,
        b.st_mtime_ns,
    ) or hashlib.sha256(data).hexdigest() != sha:
        raise IntegrityError("media_cleanup_file_changed")
    return {
        "sha256": sha,
        "byte_count": a.st_size,
        "file_key": [a.st_dev, a.st_ino],
        "mtime_ns": a.st_mtime_ns,
    }


def expected_paths(
    config: Config, db: sqlite3.Connection, job: sqlite3.Row, link: sqlite3.Row
) -> list[Path]:
    bound = media_jobs.binding(job)
    source = bound["source"]
    asset = db.execute("SELECT * FROM assets WHERE id=?", (source["asset_id"],)).fetchone()
    if asset is None or asset["sha256"] != source["sha256"]:
        raise IntegrityError("media_cleanup_asset_binding")
    paths = [
        contained(config.state_root, asset["relative_path"]),
        parent_directory(config, job["id"]) / "input/source",
        media_processing_worker.directory(config, job["id"]) / "input/source",
    ]
    download = link_directory(config, link["id"]) / "output/source"
    if download.exists():
        paths.append(download)
    return paths


def guard(config: Config, db: sqlite3.Connection, job_id: str) -> tuple[sqlite3.Row, sqlite3.Row]:
    job = db.execute("SELECT * FROM media_tasks WHERE id=?", (job_id,)).fetchone()
    if job is None:
        raise auth.Denied(404)
    vault = vaults.checked_vault(config, auth.grant(db, job["client_id"], job["vault_id"], "add"))
    publication = db.execute(
        "SELECT * FROM media_publications WHERE job_id=? AND state='published'", (job_id,)
    ).fetchone()
    link = db.execute(
        "SELECT * FROM media_link_tasks WHERE job_id=? AND state='complete'", (job_id,)
    ).fetchone()
    if (
        publication is None
        or link is None
        or link["keep_media"]
        or link["client_id"] != job["client_id"]
        or link["vault_id"] != job["vault_id"]
    ):
        raise InvalidInput("media_cleanup_linked_published_only")
    source = media_jobs.binding(job)["source"]
    binding = json.loads(link["binding_json"])
    if (binding["receipt_id"], binding["asset_id"]) != (source["receipt_id"], source["asset_id"]):
        raise IntegrityError("media_cleanup_origin_changed")
    # Shared assets and direct receipts stay retained, even when equal-byte deduplication occurred.
    if (
        db.execute(
            "SELECT count(*) FROM version_assets WHERE asset_id=?", (source["asset_id"],)
        ).fetchone()[0]
        != 1
        or (
            db.execute(
                "SELECT count(*) FROM receipts WHERE raw_asset_id=?", (source["asset_id"],)
            ).fetchone()[0]
        )
        or db.execute(
            "SELECT count(*) FROM media_tasks WHERE id!=? "
            "AND json_extract(binding_json,'$.source.asset_id')=?",
            (job_id, source["asset_id"]),
        ).fetchone()[0]
    ):
        raise InvalidInput("media_cleanup_shared_original_retained")
    manifest = json.loads(publication["manifest_json"])
    if [vault.stat().st_dev, vault.stat().st_ino] != manifest.pop("vault_key"):
        raise IntegrityError("media_cleanup_vault_changed")
    files = {}
    for name, binding in manifest.items():
        path = contained(contained(vault, publication["relative_path"]), name)
        info(path, binding["sha256"])
        files[name] = path.read_bytes()
    publication_files.verify(vault, publication["relative_path"], files)
    return job, link


def prepare_locked(config: Config, db: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    job, link = guard(config, db, job_id)
    saved = db.execute("SELECT * FROM media_cleanup WHERE job_id=?", (job_id,)).fetchone()
    if saved is not None and saved["state"] in ("deleting", "complete"):
        return summary(job_id, saved["manifest_json"], saved["state"])
    value = media_processing.result_locked(config, db, job["client_id"], job_id)
    from . import media_review

    if not media_review.gate(value, media_review.assessment(db, job_id))["publication_ready"]:
        raise InvalidInput("media_cleanup_review_required")
    source = media_jobs.binding(job)["source"]
    paths = expected_paths(config, db, job, link)
    manifest = {
        "asset_id": source["asset_id"],
        "source_sha256": source["sha256"],
        "source_object_deleted": True,
        "files": {str(p.relative_to(config.state_root)): info(p, source["sha256"]) for p in paths},
    }
    encoded = canonical(manifest).decode()
    row = db.execute("SELECT * FROM media_cleanup WHERE job_id=?", (job_id,)).fetchone()
    if row is not None:
        if row["manifest_json"] != encoded:
            raise IntegrityError("media_cleanup_plan_changed")
    else:
        db.execute(
            "INSERT INTO media_cleanup VALUES(?,?,'prepared',?)", (job_id, encoded, time.time())
        )
    return summary(job_id, encoded, "prepared")


def summary(job_id: str, encoded: str, state: str) -> dict[str, Any]:
    manifest = json.loads(encoded)
    return {
        "job_id": job_id,
        "state": state,
        "confirmation_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "file_count": len(manifest["files"]),
        "bytes": sum(v["byte_count"] for v in manifest["files"].values()),
        "direct_originals_retained": True,
        "transcripts_frames_provenance_retained": True,
    }


def apply_locked(
    config: Config, db: sqlite3.Connection, job_id: str, confirmation_sha256: str
) -> dict[str, Any]:
    job, link = guard(config, db, job_id)
    row = db.execute("SELECT * FROM media_cleanup WHERE job_id=?", (job_id,)).fetchone()
    if (
        row is None
        or hashlib.sha256(row["manifest_json"].encode()).hexdigest() != confirmation_sha256
    ):
        raise InvalidInput("media_cleanup_confirmation_required")
    manifest = json.loads(row["manifest_json"])
    bound = media_jobs.binding(job)["source"]
    # Permit explicit recovery of already-missing planned files, never substitute another path.
    allowed = {str(p.relative_to(config.state_root)) for p in expected_paths(config, db, job, link)}
    recorded = set(manifest["files"])
    download = str(
        (link_directory(config, link["id"]) / "output/source").relative_to(config.state_root)
    )
    if (
        recorded not in (allowed, allowed | {download})
        or manifest["asset_id"] != bound["asset_id"]
        or manifest["source_sha256"] != bound["sha256"]
    ):
        raise IntegrityError("media_cleanup_scope_changed")
    for name, item in manifest["files"].items():
        p = contained(config.state_root, name)
        if p.exists():
            if info(p, item["sha256"]) != item:
                raise IntegrityError("media_cleanup_file_changed")
        elif row["state"] == "prepared":
            raise IntegrityError("media_cleanup_file_missing_before_claim")
    if row["state"] == "complete":
        if any(contained(config.state_root, n).exists() for n in recorded):
            raise IntegrityError("media_cleanup_reappeared")
        return {"job_id": job_id, "state": "complete", "historical": True}
    db.execute("UPDATE media_cleanup SET state='deleting' WHERE job_id=?", (job_id,))
    for name, item in manifest["files"].items():
        p = contained(config.state_root, name)
        if p.exists():
            if info(p, item["sha256"]) != item:
                raise IntegrityError("media_cleanup_file_changed")
            os.unlink(p)
            sync_directory(p.parent)
    db.execute("UPDATE media_cleanup SET state='complete' WHERE job_id=?", (job_id,))
    return {"job_id": job_id, "state": "complete", "transcripts_frames_provenance_retained": True}


def archived_result(
    config: Config, db: sqlite3.Connection, cid: str, job_id: str
) -> dict[str, Any] | None:
    cleaned = db.execute(
        "SELECT * FROM media_cleanup WHERE job_id=? AND state='complete'", (job_id,)
    ).fetchone()
    if cleaned is None:
        return None
    job = media_jobs.load(db, cid, job_id)
    vaults.checked_vault(config, auth.grant(db, cid, job["vault_id"], "query"))
    row = media_processing.get(db, job_id)
    if row["state"] != "complete":
        raise IntegrityError("media_cleaned_result_state")
    spec = json.loads(row["plan_json"])
    root = media_processing_worker.directory(config, job_id)
    raw_bytes = read(root / "output", "result.json")
    if hashlib.sha256(raw_bytes).hexdigest() != row["result_sha256"]:
        raise IntegrityError("media_cleaned_result_changed")
    value = json.loads(raw_bytes)
    raw = json.loads(read(root / "output", "raw.json", 256 * 1024)) if spec["asr"] else None
    subtitle = read(root / "input", "subtitle", 65536) if not spec["asr"] else None
    expected = candidate(spec, raw["chunks"] if raw else None, subtitle, value["frames"])
    if expected != value:
        raise IntegrityError("media_cleaned_candidate_binding")
    from .media_subtitles import normalized

    if value["transcript"]["cues"] and read(root / "output", "normalized.srt", 65536) != normalized(
        value["transcript"]["cues"]
    ):
        raise IntegrityError("media_cleaned_normalized_changed")
    for f in value["frames"]:
        import base64

        if read(root / "output", f["name"], 65536) != base64.b64decode(f["data"], validate=True):
            raise IntegrityError("media_cleaned_frame_changed")
    return {
        **value,
        "result_sha256": row["result_sha256"],
        "glossary_revision": spec["glossary_revision"],
        "glossary_sha256": spec["glossary_sha256"],
        "job_id": job_id,
        "source_retention": "cleaned_after_publication",
        "source_bytes_verified": False,
        "historical": True,
    }
