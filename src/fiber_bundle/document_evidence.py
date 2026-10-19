"""Read-only, owner-bound PDF evidence with physical-page and text-offset locators.

Call under the storage lock, including delivery, so cancellation and revocation
cannot race a response. Candidate search stays separate from published note search.
"""

import hashlib
import json
import sqlite3
from typing import Any

from . import auth, document_bindings, document_review, document_tasks, search_matching, vaults
from .config import Config, IntegrityError, InvalidInput
from .document_contract import DocumentError


def load(
    config: Config, db: sqlite3.Connection, client_id: str, run_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    run = db.execute(
        "SELECT * FROM execution_runs WHERE id=? AND client_id=?", (run_id, client_id)
    ).fetchone()
    task = document_bindings.get(db, run_id)
    if run is None or task is None:
        raise auth.Denied(404)
    # Check the requested scope before any original/candidate bytes are read.
    vaults.checked_vault(config, auth.grant(db, client_id, run["vault_id"], "query"))
    receipt = db.execute(
        "SELECT r.source_version_id,s.vault_id FROM receipts r "
        "JOIN source_versions v ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id "
        "WHERE r.job_id=? AND r.owner_client_id=? AND r.state='stored'",
        (task["receipt_id"], client_id),
    ).fetchone()
    if receipt is None or (receipt[0], receipt[1]) != (run["version_id"], run["vault_id"]):
        raise auth.Denied(404)
    if run["state"] != "succeeded" or task["state"] != "extracted":
        raise InvalidInput("document evidence not available")
    source = document_bindings.authorize(config, db, run, task, permission="query")
    try:
        candidate = document_tasks.verified_files(config, task, source.file)
    except DocumentError as exc:
        raise IntegrityError("document evidence integrity") from exc
    from .source_processing_contract import check_result
    from .source_task_policy import frozen

    plan = frozen(db, run_id)
    if plan is not None:
        check_result(plan, candidate)
    review = document_review.latest(db, run_id)
    assessment = None if review is None else json.loads(review["assessment_json"])
    metadata = {
        "schema": "pdf-candidate-evidence/v1"
        if candidate["schema"] == 1
        else "document-material-evidence/v1",
        "run_id": run_id,
        "source_kind": "document_candidate",
        "format": candidate["format"],
        "profile": task["profile"],
        "vault_id": source.vault_id,
        "source_version_id": source.version_id,
        "asset_id": task["asset_id"],
        "source_sha256": source.sha256,
        "result_sha256": candidate["result_sha256"],
        "published": False,
        "knowledge_verified": False,
        "review_gate": document_review.gate(candidate, assessment),
    }
    if plan is not None and plan["format"] == "markdown":
        from .source_import_markdown import references

        metadata["source_format"] = "markdown"
        from .source_import_references import bound

        metadata["import_references"] = bound(config, db, run_id)
        metadata["missing_references"] = [
            r
            for r in references("\n".join(u["text"] for u in candidate["segments"]))
            if r not in metadata["import_references"]
        ]
    system_review = db.execute(
        "SELECT a.* FROM automatic_reviews a JOIN automatic_jobs j ON j.id"
        "=a.job_id WHERE json_extract(a.value,'$.task_id')=? AND j.kind='d"
        "ocument' ORDER BY a.rowid DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if system_review is not None:
        metadata["system_review"] = {
            "id": system_review["id"],
            "actor": system_review["actor"],
            **json.loads(system_review["value"]),
        }
    if metadata.get("missing_references"):
        metadata["review_gate"]["publication_ready"] = False
        metadata["review_gate"]["reasons"] = sorted(
            set(metadata["review_gate"]["reasons"] + ["missing_attachment_mapping"])
        )
    if candidate["schema"] == 1:
        metadata.update(page_count=candidate["page_count"], coverage=candidate["coverage"])
    else:
        metadata.update(
            unit_count=len(candidate["segments"]),
            complete=candidate["complete"],
            runtime=candidate["runtime"],
            attachment_count=len(candidate["attachments"]),
        )
    return metadata, candidate


def overview(config: Config, db: sqlite3.Connection, client_id: str, run_id: str) -> dict[str, Any]:
    metadata, _ = load(config, db, client_id, run_id)
    return metadata


def page(
    config: Config, db: sqlite3.Connection, client_id: str, run_id: str, physical_page: int
) -> dict[str, Any]:
    if type(physical_page) is not int or not 1 <= physical_page <= 300:
        raise InvalidInput("invalid physical page")
    metadata, candidate = load(config, db, client_id, run_id)
    if candidate["schema"] != 1:
        raise InvalidInput("use document segments for this format")
    if physical_page > candidate["page_count"]:
        raise auth.Denied(404)
    return {**metadata, "page": candidate["pages"][physical_page - 1]}


def segment(
    config: Config, db: sqlite3.Connection, client_id: str, run_id: str, unit_id: str
) -> dict[str, Any]:
    metadata, candidate = load(config, db, client_id, run_id)
    if candidate["schema"] != 2:
        raise InvalidInput("use physical pages for native PDF")
    for item in candidate["segments"]:
        if item["id"] == unit_id:
            return {**metadata, "segment": item}
    raise auth.Denied(404)


def search(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    run_id: str,
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    patterns = search_matching.terms(query)
    if type(limit) is not int or not 1 <= limit <= 20:
        raise InvalidInput("invalid document evidence limit")
    metadata, candidate = load(config, db, client_id, run_id)
    hits = []
    items = candidate["pages"] if candidate["schema"] == 1 else candidate["segments"]
    for item in items:
        from itertools import chain

        from .note_index import adjacent_passages, passages

        lines = item["text"].splitlines(keepends=True)
        offsets = [0]
        for raw in lines:
            offsets.append(offsets[-1] + len(raw))
        for line_number, snippet in chain(passages(item["text"]), adjacent_passages(item["text"])):
            if not all(pattern.search(search_matching.fold(snippet)) for pattern in patterns):
                continue
            # Locate within its physical line/window, retaining exact source character offsets.
            start_char = item["text"].find(snippet, offsets[line_number - 1])
            if start_char < 0:
                raise InvalidInput("document quote not located")
            hit = {
                "text_sha256": item["text_sha256"],
                "line": line_number,
                "line_end": line_number + len(snippet.splitlines()) - 1,
                "start_char": start_char,
                "end_char": start_char + len(snippet),
                "snippet": snippet,
                "snippet_sha256": hashlib.sha256(snippet.encode()).hexdigest(),
                "review_reasons": item["review_reasons"],
            }
            if candidate["schema"] == 1:
                hit.update(
                    citation=f"[P{item['physical_page']}]",
                    physical_page=item["physical_page"],
                    pdf_page_label=item["pdf_page_label"],
                    printed_page_label_candidate=item["printed_page_label_candidate"],
                    coordinate_status=item["coordinate_status"],
                    native_status=item["native_status"],
                    ocr_reasons=item["ocr_reasons"],
                )
            else:
                hit.update(
                    citation=f"[{item['id']}]",
                    unit_id=item["id"],
                    locator=item["locator"],
                    ocr_required=item["ocr_required"],
                )
            hits.append(hit)
            break
        if len(hits) == limit:
            break
    return {**metadata, "hits": hits, "model_used": False}
