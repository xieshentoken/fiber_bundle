"""Article receipt ownership and fixed model contract. No orchestration or publication."""

import sqlite3
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from . import auth, objects, vaults, weixin_task_guard
from .article_worker import CapturePolicy
from .articles import public_url
from .config import Config, InvalidInput

ARTICLE_PROMPT = (
    "Call fiber_capture_article with no arguments exactly once to capture the host-bound input. "
    "When the tool confirms captured, reply OK. Do not read, write, or request article content."
)
TOOL = {
    "type": "function",
    "function": {
        "name": "fiber_capture_article",
        "description": (
            "Capture the already authorized, host-bound article. Returns only status and counts."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


@dataclass(frozen=True)
class ArticleInput:
    receipt_id: str
    policy: CapturePolicy


@dataclass(frozen=True)
class ReceiptSource:
    version_id: str
    vault_id: str
    url: str


def get(db: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    row = db.execute("SELECT * FROM article_tasks WHERE run_id=?", (run_id,)).fetchone()
    return row if isinstance(row, sqlite3.Row) else None


def status(task: sqlite3.Row) -> dict[str, Any]:
    return {
        "state": task["state"],
        "image_count": task["image_count"],
        "markdown_bytes": task["markdown_bytes"],
        "published": False,
    }


def receipt_source(
    config: Config, db: sqlite3.Connection, client_id: str, job_id: str
) -> ReceiptSource:
    row = db.execute(
        "SELECT r.source_version_id,s.vault_id FROM receipts r "
        "JOIN source_versions v ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id "
        "WHERE r.job_id=? AND r.owner_client_id=? AND r.state='stored'",
        (job_id, client_id),
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    active = db.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
    if active is None or not active[0]:
        raise auth.Denied(401)
    vaults.checked_vault(config, auth.grant(db, client_id, row["vault_id"], "add"))
    assets = db.execute(
        "SELECT a.*,va.media_type FROM version_assets va JOIN assets a ON a.id=va.asset_id "
        "WHERE va.version_id=? AND va.role='body' AND a.vault_id=?",
        (row["source_version_id"], row["vault_id"]),
    ).fetchall()
    if len(assets) != 1 or assets[0]["media_type"] != "text/uri-list":
        raise InvalidInput("article receipt requires one text/uri-list body")
    asset = assets[0]
    if not 1 <= asset["byte_count"] <= 4096:
        raise InvalidInput("article URL byte limit exceeded")
    relative = objects.object_relative(row["vault_id"], asset["sha256"])
    if asset["relative_path"] != relative:
        raise InvalidInput("invalid article source location")
    data = objects.read_object(config.state_root, relative, asset["sha256"], asset["byte_count"])
    url = public_url(data.decode("utf-8"))
    return ReceiptSource(row["source_version_id"], row["vault_id"], url)


def bind(config: Config, db: sqlite3.Connection, run: sqlite3.Row, article: ArticleInput) -> None:
    article.policy.validate()
    source = receipt_source(config, db, run["client_id"], article.receipt_id)
    if source.version_id != run["version_id"] or source.vault_id != run["vault_id"]:
        raise auth.Denied(404)
    db.execute(
        "INSERT INTO article_tasks(run_id,receipt_id,source_url,worker_image,worker_timeout,"
        "capture_task_id,state) VALUES(?,?,?,?,?,?,'queued')",
        (
            run["id"],
            article.receipt_id,
            source.url,
            article.policy.image,
            article.policy.timeout_seconds,
            str(uuid4()),
        ),
    )

    from .automatic_jobs import enqueue

    enqueue(config, db, run["client_id"], run["vault_id"], "article", run["id"])


def authorize(config: Config, db: sqlite3.Connection, run: sqlite3.Row, task: sqlite3.Row) -> None:
    from .source_task_guard import authorize as authorize_source

    authorize_source(config, db, run)
    weixin_task_guard.authorize(config, db, run, task, "article")
    source = receipt_source(config, db, run["client_id"], task["receipt_id"])
    if (
        source.version_id != run["version_id"]
        or source.vault_id != run["vault_id"]
        or source.url != task["source_url"]
    ):
        raise auth.Denied(404)


def model_request(task: sqlite3.Row) -> dict[str, Any]:
    """Never forward Pi-provided messages, tools, URLs, paths or article bytes."""
    if task["state"] == "captured":
        return {
            "messages": [
                {"role": "user", "content": "The host confirms capture completed. Reply OK."}
            ]
        }
    if task["state"] != "queued":
        raise InvalidInput("article capture unavailable")
    return {"messages": [{"role": "user", "content": ARTICLE_PROMPT}], "tools": [TOOL]}
