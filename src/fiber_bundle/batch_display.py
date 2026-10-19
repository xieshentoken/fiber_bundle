"""Read-only batch stage, verified attachment counts and bounded text preview."""

import sqlite3
from typing import Any

from . import database, weixin_recovery, weixin_store_inputs
from .config import Config, InvalidInput
from .weixin_events import Binding


def table(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def project(
    config: Config, db: sqlite3.Connection, binding: Binding, view: dict[str, Any]
) -> dict[str, Any]:
    platform = "feishu" if binding.account_id.startswith("feishu:") else "weixin"
    files = archive_members = 0
    texts: list[str] = []
    attachments = []
    for index, member in enumerate(view["members"]):
        _, media, required, state = weixin_recovery.load(db, binding, member["job_id"])
        files += len(media)
        if state != "pending":
            attachments.append({"state": state, "missing_files": len(required - media.keys())})
            continue
        try:
            _, assets = weixin_store_inputs.source(db, binding, member["job_id"], index)
        except InvalidInput:
            attachments.append({"state": "unsupported"})
            continue
        for asset in assets:
            descriptor = asset.descriptor()
            attachments.append(
                {
                    "name": descriptor["original_name"],
                    "media_type": descriptor["media_type"],
                    "bytes": descriptor["byte_count"],
                    "state": "verified",
                }
            )
            if asset.original_name.startswith("zip-member-"):
                archive_members += 1
            if asset.media_type == "text/plain" and asset.data is not None and len(texts) < 3:
                try:
                    texts.append(asset.data[:4096].decode("utf-8-sig", errors="ignore")[:600])
                except UnicodeError:
                    pass
    state = "not_started"
    receipts = []
    if table(db, "wx_store_handoffs"):
        from .weixin_store import read_locked

        handoff = db.execute(
            "SELECT 1 FROM wx_store_handoffs WHERE batch_id=?", (view["batch_id"],)
        ).fetchone()
        if handoff:
            saved = read_locked(db, binding, view["batch_id"])
            state, receipts = saved["store_state"], saved["receipts"]
    result = {
        "platform": platform,
        "client_id": binding.client_id,
        "vault_id": binding.vault_id,
        "event_count": len(view["members"]),
        "received_file_count": files,
        "archive_member_count": archive_members,
        "attachments": attachments[:100],
        "store_state": state,
        "receipts": receipts,
        "preview": "\n".join(texts)[:1200] or "无已验证的文本预览；请核对原件。",
        "chat_identity": "unknown",
        "chat_message_count": None,
        "chat_complete": None,
        "publication_state": "not_published",
        "processing_state": "not_submitted",
        "tasks": [],
        "processing_blocker": None,
    }
    main = database.connect(config.state_root)
    try:
        client = main.execute(
            "SELECT name,active FROM clients WHERE id=?", (binding.client_id,)
        ).fetchone()
        vault = main.execute("SELECT name FROM vaults WHERE id=?", (binding.vault_id,)).fetchone()
        result["client_name"] = client["name"] if client else "已不可用"
        result["vault_name"] = vault["name"] if vault else "已不可用"
        runs = {
            r[0]
            for r in main.execute(
                "SELECT run_id FROM source_task_bindings WHERE batch_id=? AND account_id=?",
                (view["batch_id"], binding.account_id),
            )
        }
        if table(db, "wx_task_bindings"):
            runs.update(
                r[0]
                for r in db.execute(
                    "SELECT run_id FROM wx_task_bindings WHERE batch_id=? AND account_id=?",
                    (view["batch_id"], binding.account_id),
                )
            )
        tasks = []
        for rid in sorted(runs):
            run = main.execute("SELECT state FROM execution_runs WHERE id=?", (rid,)).fetchone()
            document = main.execute(
                "SELECT state FROM document_tasks WHERE run_id=?", (rid,)
            ).fetchone()
            article = main.execute(
                "SELECT state FROM article_tasks WHERE run_id=?", (rid,)
            ).fetchone()
            published = main.execute(
                "SELECT state FROM document_publications WHERE run_id=? UNION ALL "
                "SELECT p.state FROM article_publication_runs r JOIN article_publications p "
                "ON p.id=r.publication_id WHERE r.run_id=?",
                (rid, rid),
            ).fetchall()
            tasks.append(
                {
                    "run_id": rid,
                    "state": run[0] if run else "missing",
                    "candidate_state": document[0] if document else article[0] if article else None,
                    "published": any(p[0] == "published" for p in published),
                }
            )
        result["tasks"] = tasks
        if tasks:
            states = {t["state"] for t in tasks}
            result["processing_state"] = next(iter(states)) if len(states) == 1 else "mixed"
            total = sum(t["published"] for t in tasks)
            result["publication_state"] = (
                "published" if total == len(tasks) else "partial" if total else "not_published"
            )
        from .channels.batch_intake import progress

        intake = progress(config, main, binding, view["batch_id"])
        result["intake"] = intake
        if intake:
            links = intake["links"]
            total = sum(link["published"] for link in links)
            if total:
                result["publication_state"] = "published" if total == len(links) else "partial"

    finally:
        main.close()
    if platform == "feishu" and state != "stored" and view["state"] == "selected":
        result["processing_blocker"] = "requires_explicit_store_quota"
    if platform == "feishu":
        health = gateway_health(config, binding.account_id)
        result["receiver_health"] = health
        if state != "stored" and view["state"] == "selected":
            if not health.get("available"):
                result["processing_blocker"] = "receiver_unavailable"
            elif health.get("store_remaining") == 0:
                result["processing_blocker"] = "store_quota_zero"
            else:
                result["processing_blocker"] = None
        if result.get("intake") is not None:
            # Managed intake has its own versioned limits; the legacy receiver's
            # Store counter stays zero and must not masquerade as a v2 blocker.
            result["processing_blocker"] = None
    return result


def gateway_health(config: Config, account: str) -> dict[str, Any]:
    """Only validated leases count; bounded prepared home enumeration reads no credentials."""
    import os

    from .feishu_runtime import inspect

    root = config.state_root / "configs"
    if not root.is_dir() or root.is_symlink():
        return {"available": False, "state": "unavailable"}
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 32:
                break
            if entry.is_dir(follow_symlinks=False):
                from pathlib import Path

                health = inspect(Path(entry.path))
                if health.get("account_id") == account:
                    return health
    return {"available": False, "state": "unavailable"}
