"""GUI source review of captured articles; publication remains an explicit separate action."""

import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import article_bindings, article_publication, article_tasks, auth, execution_ledger
from .article_capture import VerifiedCapture, read_bounded, verify_capture
from .config import Config, InvalidInput
from .inputs import canonical
from .objects import digest


def capture(
    config: Config, db: sqlite3.Connection, run_id: str
) -> tuple[sqlite3.Row, sqlite3.Row, VerifiedCapture]:
    run = execution_ledger.get_run(db, run_id)
    task = article_bindings.get(db, run_id)
    if task is None or task["state"] != "captured" or run["state"] != "succeeded":
        raise InvalidInput("article candidate unavailable")
    article_bindings.authorize(config, db, run, task)
    auth.grant(db, run["client_id"], run["vault_id"], "original")
    root = article_tasks.output_path(config, task)
    if digest(read_bounded(root, "capture.json", 65536)) != task["manifest_sha256"]:
        raise InvalidInput("article manifest changed")
    verified = verify_capture(root, task["source_url"])
    return run, task, verified


def packet(config: Config, db: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    run, task, verified = capture(config, db, run_id)
    article = verified.draft.article
    review = db.execute(
        "SELECT * FROM article_reviews WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (run_id,)
    ).fetchone()
    publication = db.execute(
        "SELECT p.id,p.relative_path,p.state FROM article_publication_runs r JOIN "
        "article_publications p ON p.id=r.publication_id WHERE r.run_id=?",
        (run_id,),
    ).fetchone()
    from .publication_locations import wire

    return {
        "run_id": run_id,
        "client_id": run["client_id"],
        "vault_id": run["vault_id"],
        "title": article.title,
        "author": article.author,
        "published": (article.date_evidence or {}).get("value", ""),
        "date_evidence": article.date_evidence,
        "markdown": verified.draft.markdown,
        "source_sha256": article.source_sha256,
        "manifest_sha256": task["manifest_sha256"],
        "images": [
            {"index": i.index, "sha256": i.sha256, "relative_path": i.relative_path}
            for i in verified.draft.images
        ],
        "review_ready": review is not None and review["manifest_sha256"] == task["manifest_sha256"],
        "review_id": review["id"] if review else None,
        "publication": {
            **dict(publication),
            **wire(db, "article", publication["id"]),
        }
        if publication
        else None,
        "knowledge_verified": False,
    }


def record(
    config: Config, db: sqlite3.Connection, run_id: str, assessment: dict[str, Any]
) -> dict[str, Any]:
    _, task, _ = capture(config, db, run_id)
    if any(
        assessment.get(k) is not True
        for k in ("source_reviewed", "text_reviewed", "images_reviewed")
    ):
        raise InvalidInput("article review incomplete")
    if assessment["manifest_sha256"] != task["manifest_sha256"] or not assessment["reason"].strip():
        raise InvalidInput("article review changed or missing reason")
    if db.execute("SELECT 1 FROM article_publication_runs WHERE run_id=?", (run_id,)).fetchone():
        raise InvalidInput("published article review frozen")
    db.execute(
        "INSERT INTO article_reviews VALUES(?,?,?,?,?)",
        (str(uuid4()), run_id, task["manifest_sha256"], canonical(assessment), time.time()),
    )
    return packet(config, db, run_id)


def publish(
    config: Config,
    db: sqlite3.Connection,
    run_id: str,
    review_id: str,
    expected: str,
    *,
    preview_only: bool = False,
) -> dict[str, Any]:
    run, task, _ = capture(config, db, run_id)
    row = db.execute(
        "SELECT * FROM article_reviews WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (run_id,)
    ).fetchone()
    if (
        row is None
        or row["id"] != review_id
        or row["manifest_sha256"] != expected
        or expected != task["manifest_sha256"]
    ):
        raise InvalidInput("article review gate not current")
    return article_publication.publish_locked(
        config, db, run["client_id"], run_id, preview_only=preview_only
    )


def listing(db: sqlite3.Connection) -> dict[str, Any]:
    from .task_observations import automatic

    return {
        "tasks": automatic(
            db,
            "article",
            [
                dict(r)
                for r in db.execute(
                    "SELECT a.run_id,a.state,r.state AS "
                    "run_state,r.client_id,r.vault_id,a.image_count,p.state AS publication_state "
                    "FROM article_tasks a JOIN execution_runs r ON r.id=a.run_id LEFT JOIN "
                    "article_publication_runs b ON b.run_id=a.run_id "
                    "LEFT JOIN article_publications p ON p.id=b.publication_id "
                    "ORDER BY r.created_at DESC LIMIT 50"
                )
            ],
        )
    }
