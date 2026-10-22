"""Observe newly published channel/article notes without changing their files or review rules."""

import time
from datetime import datetime
from typing import Any

from .. import auth, note_inventory, note_provenance
from ..config import Config, InvalidInput
from . import ingestion, service, store


def enqueue(config: Config, db: Any, client: str, vault: str, after: float) -> int:
    row = service.authorize(config, db, client, vault, write=True)
    auth.grant(db, client, vault, "original")
    inventory = note_inventory.read(config, db, row, registered=False)
    count = 0
    for note in inventory.notes:
        # A changed historical note is never implicitly an instruction to re-extract it.
        if (inventory.root / note.relative_path).stat().st_mtime < after:
            continue
        proof = note_provenance.describe(config, db, client, row, inventory, note)
        version = proof.get("source_version_id")
        if not version or proof["source_kind"] not in {
            "published_channel_content",
            "published_article",
        }:
            continue
        receipt = db.execute(
            "SELECT job_id,created_at,owner_client_id FROM receipts "
            "WHERE source_version_id=? AND state='stored' ORDER BY created_at LIMIT 1",
            (version,),
        ).fetchone()
        if receipt is None or not receipt["owner_client_id"]:
            continue
        created = datetime.fromisoformat(receipt["created_at"].replace("Z", "+00:00")).timestamp()
        if created < after:
            continue
        identity = "publication-" + ingestion.stable("note", vault, version, note.sha256)
        if store.get(config, vault, "job", identity):
            continue
        source = {
            "id": ingestion.stable("source", vault, identity),
            "title": note.title,
            "sha256": note.sha256,
            "text": note.text,
            "legacy": False,
            "original_state": "retained",
            "knowledge_verified": False,
            "version_id": version,
            "receipt_id": receipt["job_id"],
            "owner_client_id": receipt["owner_client_id"],
            "captured_at": time.time(),
            "path": note.relative_path,
            "observation": service.observation(inventory.root / note.relative_path),
            "source_kind": proof["source_kind"],
        }
        ingestion.current_source(config, db, source["owner_client_id"], vault, source)
        if not note.text.strip():
            raise InvalidInput("supplier_source_text_empty")
        job = {
            "id": identity,
            "vault_id": vault,
            "client_id": source["owner_client_id"],
            "supplier_id": None,
            "source": source,
            "state": "queued",
            "created_at": time.time(),
            "windows": [],
            "error": None,
        }
        with store.connection(config, write=True) as side:
            assert side is not None
            store.put(side, vault, "source", source)
            store.put(side, vault, "job", job)
        count += 1
    return count


def fingerprint(db: Any, vault: str) -> tuple[int, ...]:
    # Cheap publication metadata prevents repeated full-vault reads while idle.
    article = db.execute(
        "SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM article_publications "
        "WHERE vault_id=? AND state='published'",
        (vault,),
    ).fetchone()
    intake = db.execute(
        "SELECT COUNT(*),COALESCE(MAX(rowid),0),COALESCE(SUM(revision),0) "
        "FROM publication_locations WHERE vault_id=? AND kind IN ('intake','article') "
        "AND state='published'",
        (vault,),
    ).fetchone()
    return tuple(article) + tuple(intake)
