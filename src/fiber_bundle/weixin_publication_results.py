"""Verify completed publication receipts without writing, adopting or repairing files."""

import json
import sqlite3
from typing import Any

from . import article_publication, article_tasks, publication_files, publication_locations
from . import weixin_publication as publication
from .article_capture import read_bounded, verify_capture
from .config import Config, IntegrityError, InvalidInput
from .inputs import canonical
from .objects import digest
from .weixin_events import Binding


class PublicationPending(InvalidInput):
    """An intended publication has not completed; result delivery must wait."""


def result_locked(
    config: Config,
    journal: sqlite3.Connection,
    db: sqlite3.Connection,
    binding: Binding,
    batch_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    ids = publication.planned_locked(journal, binding, batch_id)
    if not ids:
        return None
    if run_id not in ids:
        raise IntegrityError("publication task scope changed")
    row = journal.execute("SELECT * FROM wx_task_publications WHERE run_id=?", (run_id,)).fetchone()
    if row["phase"] != "published":
        raise PublicationPending("article publication not ready")
    if not row["result_json"] or digest(row["result_json"].encode()) != row["result_sha256"]:
        raise IntegrityError("publication result changed")
    result = json.loads(row["result_json"])
    _, task, vault = article_publication.bound(config, db, binding.client_id, run_id)
    saved = db.execute(
        "SELECT p.*,r.archive_receipt_id FROM article_publication_runs r "
        "JOIN article_publications p ON p.id=r.publication_id WHERE r.run_id=?",
        (run_id,),
    ).fetchone()
    location = publication_locations.get(db, "article", saved["id"]) if saved else None
    expected_path = (
        location["entry_note_path"]
        if location
        else saved["relative_path"] + "/note.md"
        if saved
        else None
    )
    receipt = db.execute(
        "SELECT source_version_id FROM receipts WHERE job_id=? AND owner_client_id=?",
        (result["archive_receipt_id"], binding.client_id),
    ).fetchone()
    if (
        saved is None
        or receipt is None
        or (
            result.get("state") != "published"
            or result.get("published") is not True
            or result.get("vault_id") != binding.vault_id
            or receipt["source_version_id"] != result["archive_version_id"]
            or saved["state"] != "published"
            or saved["id"] != result["publication_id"]
            or saved["version_id"] != result["source_version_id"]
            or saved["archive_receipt_id"] != result["archive_receipt_id"]
            or saved["vault_id"] != binding.vault_id
            or (location is None and result["note_path"] != expected_path)
        )
    ):
        raise IntegrityError("publication ledger changed")
    article_publication.check_vault_identity(vault, (saved["vault_dev"], saved["vault_ino"]))
    output = article_tasks.output_path(config, task)
    if digest(read_bounded(output, "capture.json", 65536)) != task["manifest_sha256"]:
        raise IntegrityError("capture manifest changed")
    files = article_publication.content_files(verify_capture(output, task["source_url"]))
    descriptors = article_publication.descriptors(files)
    if canonical(descriptors) != saved["manifest_json"]:
        raise IntegrityError("publication content changed")
    if location:
        if location["state"] != "published":
            raise PublicationPending("article publication not ready")
        # Delivery reports the current location; the immutable receipt keeps its original path.
        from .publication_file_io import read

        current, key = read(vault, location["entry_note_path"])
        expected_key = location["plan"]["file_keys"][location["entry_note_path"]]
        if key != expected_key:
            raise IntegrityError("publication physical identity changed")
        return {
            **result,
            "note_path": location["entry_note_path"],
            **publication_locations.wire(db, "article", saved["id"]),
        }
    files[".fiber-publication.json"] = canonical(
        {"publication_id": saved["id"], "version_id": saved["version_id"], "files": descriptors}
    ).encode()
    publication_files.verify(vault, saved["relative_path"], files)
    return dict(result)
