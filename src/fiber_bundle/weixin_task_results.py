"""Authorized, bounded batch result text; source and model output are never instructions."""

import sqlite3
from typing import Any

from . import answer_tasks, article_bindings, database, execution_ledger, local_query_tasks
from . import weixin_publication_results as publication
from . import weixin_store as handoff
from .config import Config, InvalidInput
from .inputs import canonical
from .objects import digest
from .weixin_events import Binding
from .weixin_note_queries import identity as note_identity

MAX_TEXT_BYTES = 8192


class ResultPending(InvalidInput):
    """Expected incomplete work; schedulers may skip without suppressing authorization errors."""


def snapshot_locked(
    config: Config, journal: sqlite3.Connection, binding: Binding, batch_id: str
) -> tuple[str, str]:
    """Caller holds the authorized inbox/storage lock. Return text and complete result digest."""
    view = handoff.read_locked(journal, binding, batch_id)
    if view["batch_state"] != "selected" or view["store_state"] != "stored":
        raise InvalidInput("batch result unavailable")
    if not journal.execute("SELECT 1 FROM sqlite_master WHERE name='wx_task_bindings'").fetchone():
        raise ResultPending("batch has no submitted tasks")
    tasks = journal.execute(
        "SELECT * FROM wx_task_bindings WHERE account_id=? AND batch_id=? ORDER BY rowid",
        (binding.account_id, batch_id),
    ).fetchall()
    kind = "answer" if view["action"] == "consult" or view.get("query_after_save") else "article"
    expected = (
        {note_identity(batch_id)}
        if view.get("query_existing")
        else {f"weixin-task:{batch_id}:batch:answer"}
        if view.get("query_after_save")
        else {f"weixin-task:{batch_id}:{r['job_id']}:{kind}" for r in view["receipts"]}
    )
    if not expected or len(tasks) > len(expected):
        raise InvalidInput("batch task scope changed")
    if len(tasks) < len(expected):
        raise ResultPending("batch task submissions incomplete")
    db = database.connect(config.state_root)
    try:
        actual = set()
        results: list[dict[str, Any]] = []
        sections = [f"批次 {batch_id} 任务回执"]
        for bound in tasks:
            receipt = db.execute(
                "SELECT event_id FROM receipts WHERE job_id=?", (bound["input_receipt_id"],)
            ).fetchone()
            if (receipt is None and not view.get("query_existing")) or bound["kind"] != kind:
                raise InvalidInput("batch task scope changed")
            actual.add(
                bound["input_receipt_id"] if view.get("query_existing") else receipt["event_id"]
            )
            local = db.execute(
                "SELECT id FROM local_query_tasks WHERE id=?", (bound["run_id"],)
            ).fetchone()
            if local is not None:
                run = local_query_tasks.get(db, binding.client_id, bound["run_id"])
                if bound["kind"] != "answer" or run["config_digest"] != bound["policy_digest"]:
                    raise InvalidInput("batch local policy changed")
                answer_tasks.authorize(config, db, run, run)
            else:
                if not db.execute(
                    "SELECT 1 FROM execution_runs WHERE id=?", (bound["run_id"],)
                ).fetchone():
                    raise ResultPending("batch task submission incomplete")
                run = execution_ledger.get_run(db, bound["run_id"])
                execution_ledger.authorize(config, db, run)
            if run["state"] not in {"succeeded", "failed", "cancelled"}:
                raise ResultPending("batch task results not ready")
            result: dict[str, Any] = {"run_id": run["id"], "kind": kind, "state": run["state"]}
            sections.append(f"任务 {run['id']}")
            if run["state"] != "succeeded":
                sections.append(
                    "执行失败，请在本机查看任务。" if run["state"] == "failed" else "任务已取消。"
                )
            elif kind == "article":
                task = article_bindings.get(db, run["id"])
                if task is None or task["state"] != "captured":
                    raise InvalidInput("article result incomplete")
                result["manifest_sha256"] = task["manifest_sha256"]
                try:
                    published = publication.result_locked(
                        config, journal, db, binding, batch_id, run["id"]
                    )
                except publication.PublicationPending:
                    raise ResultPending("article publication not ready") from None
                if published is None:
                    sections.append("文章提取完成。笔记发布需另行核验。")
                else:
                    result["publication"] = published
                    sections.append(
                        f"文章已发布。笔记：{published['note_path']}\n"
                        f"来源版本：{published['source_version_id']}"
                    )
            else:
                answer = (
                    local_query_tasks.deliver(config, db, binding.client_id, run["id"])
                    if local is not None
                    else answer_tasks.deliver(config, db, binding.client_id, run["id"])
                )
                if answer["mode"] != "extractive":
                    raise InvalidInput("Weixin result mode unsupported")
                result["answer"] = answer
                sections += ["问答完成。", answer["answer"], *answer["cautions"]]
                sources = {s["citation"]: s for s in answer["sources"]}
                for quotation in answer["quotations"]:
                    source = sources[quotation["citation"]]
                    sections.append(
                        f"{quotation['citation']} 来源类型：{source['source_label']}；"
                        f"材料：{source['title']}\n"
                        f"版本/指纹：{source['source_version_id'] or source['sha256']}；"
                        f"行：{source['line']}\n"
                        f"原文片段：\n{quotation['quote']}"
                    )
            results.append(result)
        if actual != expected:
            raise InvalidInput("batch task scope changed")
        body = "\n\n".join(sections)
        if len(body.encode()) > MAX_TEXT_BYTES:
            counts = {
                s: sum(r["state"] == s for r in results)
                for s in ("succeeded", "failed", "cancelled")
            }
            body = (
                f"批次 {batch_id} 任务回执\n"
                f"执行成功 {counts['succeeded']}，失败 {counts['failed']}，"
                f"取消 {counts['cancelled']}。\n"
                "完整结果超过单条消息上限，请在本机按批次查看；本条未发送引文。\n"
                "执行成功不等于笔记已发布或结论已核实。"
            )
        return body, digest(canonical(results).encode())
    finally:
        db.close()
