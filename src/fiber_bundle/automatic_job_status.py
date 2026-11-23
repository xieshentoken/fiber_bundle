"""Read-only worklist projection; historical attempts and publication records stay intact."""

import sqlite3
from pathlib import PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5


def successor(db: sqlite3.Connection, job: dict[str, Any]) -> str | None:
    row = db.execute(
        "SELECT id FROM automatic_jobs WHERE vault_id=? AND client_id=? AND kind=? "
        "AND id!=? AND created_at>=? AND json_extract(value,'$.supersedes_job')=? "
        "ORDER BY created_at DESC,id DESC LIMIT 1",
        (job["vault_id"], job["client_id"], job["kind"], job["id"], job["created_at"], job["id"]),
    ).fetchone()
    return str(row[0]) if row else None


def display_state(job: dict[str, Any]) -> str:
    state = job["state"]
    if (
        state in {"waiting", "reconcile", "needs_review"}
        and job["phase"] == "publish"
        and job.get("reason") == "placement_reconciliation_required"
    ):
        return "waiting_publication"
    if state == "waiting" and job.get("reason") in {
        "automatic_waiting_for_local_folder",
        "automatic_saved_index_updating",
    }:
        return "waiting_dependency" if job["phase"] != "index" else "index_updating"
    if state == "reviewed":
        return "awaiting_review"
    if state in {"needs_review", "reconcile"}:
        if job["phase"] == "index":
            return "index_reconcile"
        reason = job.get("reason") or ""
        if "budget" in reason:
            return "waiting_budget"
        if reason == "ocr_profile_required" or any(
            word in reason for word in ("unconfigured", "not_ready", "unavailable")
        ):
            return "waiting_dependency"
        return "reconcile" if state == "reconcile" else "failed"
    return str(state)


def location(db: sqlite3.Connection, job: dict[str, Any]) -> dict[str, Any] | None:
    from . import publication_locations

    kind, identity = job["kind"], job["source_id"]
    value = job["value"]
    if kind == "intake" and value.get("intake"):
        context = value["intake"]
        identity = str(
            uuid5(
                NAMESPACE_URL,
                "fiber-intake:"
                + context["channel"]
                + ":"
                + context["batch"]
                + ":"
                + str(context["index"]),
            )
        )
    elif kind == "document":
        if value.get("chat_archive"):
            kind = "intake"
        else:
            identity = value.get("run_id") or ""
    return publication_locations.get(db, kind, identity)


def publication(db: sqlite3.Connection, job: dict[str, Any]) -> dict[str, Any]:
    from . import publication_locations

    row = location(db, job)
    value = job["value"]
    if row and publication_locations.committed(db, row):
        return {"state": "published", "entry_note_path": row["entry_note_path"]}
    saved = value.get("publication") or {}
    return saved if saved.get("state") == "published" else {}


def project(db: sqlite3.Connection, job: dict[str, Any]) -> dict[str, Any]:
    from .processing_errors import MESSAGES
    from .publication_blockers import pending

    replacement = successor(db, job)
    state = "superseded" if replacement else display_state(job)
    blockers = pending(db, job["vault_id"]) if job["phase"] == "publish" and not replacement else []
    if blockers and job["state"] in {"waiting", "reconcile", "needs_review"}:
        state = "waiting_publication"
    saved = publication(db, job)
    path = saved.get("entry_note_path")
    message = (
        {
            "waiting_publication": (
                "正文和附件已保留，等待本库归档操作完成核对后发布；无需重新转发或提取。"
                + ("阻塞操作：" + ", ".join(b["id"] for b in blockers) if blockers else "")
            ),
            "superseded": "已有后续任务接替，旧记录保留，不需重复处理。",
            "index_reconcile": (
                "保存记录已保留，索引尚未完成；请检查知识库中已登记笔记的位置，再继续索引。"
                if saved
                else "索引尚未完成，请检查知识库路径与权限后继续索引。"
            ),
            "awaiting_review": "提取结果已准备好，等待人工确认保存。",
            "complete": "任务已完成，历史记录保留。",
            "cancelled": "后续处理已停止，已完成结果保留。",
            "queued": "已入队，由后台继续处理。",
            "claimed": "后台正在处理。",
            "waiting": "等待后台处理结果。",
            "waiting_dependency": (
                "目标目录暂未就绪；原件和提取结果已保留，后台将按退避时间重新检查。"
            ),
            "index_updating": "笔记已保存，正在更新检索索引，无需重复导入。",
        }.get(state)
        or MESSAGES.get(job.get("reason") or "")
        or {
            "model_timeout": "模型处理超时；原件与已有用量记录保留，请进入消息入口核对。",
            "intake_content_incomplete": "来源正文或附件提取未完成，请进入消息入口核对缺项。",
        }.get(job.get("reason") or "")
    )
    return {
        "display_state": state,
        "publication_blockers": blockers,
        "display_message": message or "处理未完成，请进入任务记录核对原因。",
        "superseded_by": replacement,
        "action_required": state
        in {
            "awaiting_review",
            "index_reconcile",
            "waiting_budget",
            "waiting_dependency",
            "waiting_publication",
            "needs_supplement",
            "failed",
            "reconcile",
        },
        "publication_state": saved.get("state"),
        "entry_note_path": path,
        "title": PurePosixPath(path).stem if path else None,
        "vault_id": job["vault_id"],
    }
