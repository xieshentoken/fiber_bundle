"""PDF receipt ownership, explicit asset binding and fixed content-free Pi contract."""

import sqlite3
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from . import auth, objects, vaults, weixin_task_guard
from .config import Config, IntegrityError, InvalidInput, contained
from .document_contract import MAX_SOURCE_BYTES
from .document_source import DocumentFile
from .document_worker import DocumentPolicy
from .material_contract import MIMES
from .store import Store

PROMPT = (
    "Call fiber_extract_document with no arguments exactly once "
    "to extract the host-bound document. "
    "When the tool confirms extracted, reply OK. Do not read, write, or request document content."
)
TOOL = {
    "type": "function",
    "function": {
        "name": "fiber_extract_document",
        "description": (
            "Extract the authorized host-bound document candidate. Returns only status and counts."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


@dataclass(frozen=True)
class DocumentInput:
    receipt_id: str
    asset_id: str
    policy: DocumentPolicy
    profile: str = "native"
    runtime: dict[str, Any] | None = None


@dataclass(frozen=True)
class DocumentSource:
    version_id: str
    vault_id: str
    sha256: str
    byte_count: int
    file: DocumentFile
    format: str = "pdf"

    @property
    def data(self) -> bytes:
        """Compatibility for callers explicitly requiring an in-memory original."""
        return self.file.read()


def get(db: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    row = db.execute(
        "SELECT d.*,COALESCE(p.format,'pdf') AS format,"
        "COALESCE(p.profile,'native') AS profile,p.runtime_json "
        "FROM document_tasks d LEFT JOIN document_profiles p ON p.run_id=d.run_id WHERE d.run_id=?",
        (run_id,),
    ).fetchone()
    return row if isinstance(row, sqlite3.Row) else None


def source(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    receipt_id: str,
    asset_id: str,
    *,
    permission: Literal["add", "query"] = "add",
    verify_file: bool = True,
) -> DocumentSource:
    row = db.execute(
        "SELECT r.source_version_id,s.vault_id FROM receipts r "
        "JOIN source_versions v ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id "
        "WHERE r.job_id=? AND r.owner_client_id=? AND r.state='stored'",
        (receipt_id, client_id),
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    client = db.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
    if client is None or not client[0]:
        raise auth.Denied(401)
    vaults.checked_vault(config, auth.grant(db, client_id, row["vault_id"], permission))
    Store(config).verify_receipt_locked(db, receipt_id)
    assets = db.execute(
        "SELECT a.*,va.media_type,va.role FROM assets a JOIN version_assets va ON va.asset_id=a.id "
        "WHERE va.version_id=? AND a.id=? AND a.vault_id=?",
        (row["source_version_id"], asset_id, row["vault_id"]),
    ).fetchall()
    if (
        not assets
        or len({a["media_type"] for a in assets}) != 1
        or any(
            a["media_type"] not in MIMES or a["role"] not in ("body", "attachment") for a in assets
        )
    ):
        raise InvalidInput("document receipt requires an explicit supported asset")
    asset = assets[0]
    if not 0 < asset["byte_count"] <= MAX_SOURCE_BYTES:
        raise InvalidInput("document source size")
    relative = objects.object_relative(row["vault_id"], asset["sha256"])
    if asset["relative_path"] != relative:
        raise IntegrityError("document source location")
    file = DocumentFile(
        contained(config.state_root, relative), asset["sha256"], asset["byte_count"]
    )
    if verify_file:
        file.verify()
    return DocumentSource(
        row["source_version_id"],
        row["vault_id"],
        asset["sha256"],
        asset["byte_count"],
        file,
        MIMES[asset["media_type"]],
    )


def bind(config: Config, db: sqlite3.Connection, run: sqlite3.Row, document: DocumentInput) -> None:
    document.policy.validate()
    bound = source(config, db, run["client_id"], document.receipt_id, document.asset_id)
    if (bound.version_id, bound.vault_id) != (run["version_id"], run["vault_id"]):
        raise auth.Denied(404)
    db.execute(
        "INSERT INTO document_tasks(run_id,receipt_id,asset_id,source_sha256,source_bytes,"
        "worker_image,worker_timeout,capture_task_id,state) VALUES(?,?,?,?,?,?,?,?,'queued')",
        (
            run["id"],
            document.receipt_id,
            document.asset_id,
            bound.sha256,
            bound.byte_count,
            document.policy.image,
            document.policy.timeout_seconds,
            str(uuid4()),
        ),
    )

    if document.profile not in {"native", "ocr", "conversion"} or (
        document.profile != "native"
    ) != (document.runtime is not None):
        raise InvalidInput("explicit document profile/runtime required")
    if document.profile == "ocr":
        from .document_ocr import verify_runtime

        if bound.format not in {"pdf", "image"}:
            raise InvalidInput("OCR requires PDF or image")
        assert document.runtime is not None
        verify_runtime(document.runtime)
        if document.runtime["image"] != document.policy.image:
            raise InvalidInput("OCR runtime image must match document image")
    elif document.profile == "conversion":
        from .document_conversion import verify_runtime as verify_conversion

        if bound.format != "doc" or document.runtime is None:
            raise InvalidInput("conversion requires legacy DOC")
        verify_conversion(document.runtime)
        if document.runtime["image"] != document.policy.image:
            raise InvalidInput("conversion runtime image must match document image")
    elif bound.format in {"image", "doc"}:
        raise InvalidInput("image requires explicit OCR profile")
    if bound.format != "pdf" or document.profile != "native":
        from .inputs import canonical

        db.execute(
            "INSERT INTO document_profiles(run_id,format,profile,runtime_json) VALUES(?,?,?,?)",
            (
                run["id"],
                bound.format,
                document.profile,
                None if document.runtime is None else canonical(document.runtime),
            ),
        )


def authorize(
    config: Config,
    db: sqlite3.Connection,
    run: sqlite3.Row,
    task: sqlite3.Row,
    *,
    permission: Literal["add", "query"] = "add",
    verify_file: bool = True,
) -> DocumentSource:
    # Derived Weixin tasks must also have a supported journal binding; none exist yet for PDFs.
    from .source_task_guard import authorize as authorize_source

    authorize_source(config, db, run)
    weixin_task_guard.authorize(config, db, run, task, "document")
    bound = source(
        config,
        db,
        run["client_id"],
        task["receipt_id"],
        task["asset_id"],
        permission=permission,
        verify_file=verify_file,
    )
    if (bound.version_id, bound.vault_id, bound.sha256, bound.byte_count) != (
        run["version_id"],
        run["vault_id"],
        task["source_sha256"],
        task["source_bytes"],
    ) or bound.format != task["format"]:
        raise auth.Denied(404)
    return bound


def status(task: sqlite3.Row) -> dict[str, Any]:
    summary = dict(
        state=task["state"],
        page_count=task["page_count"],
        ocr_page_count=task["ocr_page_count"],
        review_page_count=task["review_page_count"],
        published=False,
        knowledge_verified=False,
    )

    if task["format"] != "pdf" or task["profile"] != "native":
        summary["unit_count"] = summary.pop("page_count")
        summary["format"] = task["format"]
        summary["profile"] = task["profile"]
    return summary


def model_request(task: sqlite3.Row) -> dict[str, Any]:
    if task["state"] == "extracted":
        return {
            "messages": [
                {
                    "role": "user",
                    "content": "The host confirms candidate extraction completed. Reply OK.",
                }
            ]
        }
    if task["state"] != "queued":
        raise InvalidInput("document extraction unavailable")
    return {"messages": [{"role": "user", "content": PROMPT}], "tools": [TOOL]}
