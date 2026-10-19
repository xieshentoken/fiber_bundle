"""Read-only batch outcomes shared by receipts and the intake records view."""

import json
import sqlite3
from typing import Any

from .. import automatic_jobs
from ..inputs import canonical
from ..objects import digest
from ..processing_errors import MESSAGES


def reason_text(code: str | None) -> str:
    from ..model_configuration.errors import MESSAGES as model_messages

    if code in model_messages:
        return str(model_messages[code][1])
    return MESSAGES.get(code or "", "处理未完成，请在 Fiber bundle 查看本批诊断。")


def outcome(
    main: sqlite3.Connection, channel: dict[str, Any], batch: str, value: dict[str, Any]
) -> dict[str, Any] | None:
    failure = value.get("failure")
    if failure:
        code = failure.get("code")
        return {
            "key": "admission_failed:" + digest(canonical(failure).encode()),
            "stage": "failed",
            "body": f"批次 {batch}\n"
            + (
                "原消息和原件已归档，后续笔记入库未完成。\n"
                if failure.get("originals_saved")
                else "原件归档未完成，已保留收到的消息。\n"
            )
            + reason_text(code),
            "done": True,
        }
    ids = [x["job_id"] for x in value.get("automatic_imports", []) if x.get("job_id")][:100]
    marks = ",".join("?" for _ in ids) or "NULL"
    jobs = [
        {**dict(row), "value": json.loads(row["value"])}
        for row in main.execute(
            "SELECT * FROM automatic_jobs AS j WHERE client_id=? AND "
            "(json_extract(value,'$.intake.batch')=? OR "
            "json_extract(value,'$.channel_batch.batch')=? OR id IN (" + marks + ")) "
            "AND NOT EXISTS(SELECT 1 FROM automatic_jobs AS s "
            "WHERE s.vault_id=j.vault_id AND s.client_id=j.client_id AND s.kind=j.kind "
            "AND json_extract(s.value,'$.supersedes_job')=j.id) ORDER BY id",
            (channel["value"]["client_id"], batch, batch, *ids),
        )
    ]
    links = value.get("links", [])
    reasons = sorted(
        {
            x["reason"]
            for x in [*links, *jobs, *value.get("automatic_imports", [])]
            if x.get("reason") and not (x.get("publication") or {}).get("state") == "published"
        }
    )
    failed_links = [
        x
        for x in links
        if x.get("state") in {"needs_review", "link_only", "metadata_only"}
        and not (x.get("publication") or {}).get("state") == "published"
    ]
    failed_jobs = [j for j in jobs if j["state"] in {"needs_review", "reconcile"}]
    pending = any(x.get("state") in {None, "pending", "extracting"} for x in links) or any(
        j["state"] in {"queued", "claimed", "waiting"} for j in jobs
    )
    index_failure = any(j["phase"] == "index" for j in failed_jobs)
    all_published = (
        bool(links or jobs)
        and all((x.get("publication") or {}).get("state") == "published" for x in links)
        and all(
            j["state"] == "complete" or (j["phase"] == "index" and j["value"].get("publication"))
            for j in jobs
        )
    )
    if value.get("aborted"):
        stage, text = (
            "cancelled",
            "本批已取消；原消息、已提交调用费用及已经入库的笔记保留。",
        )
    elif all_published:
        stage = "index_failed" if index_failure else "saved"
        text = (
            "笔记已保存入库，检索索引更新失败；请在 Fiber bundle 恢复索引。"
            if index_failure
            else (
                "笔记已保存入库，正文及附件按知识库保存规则写入。"
                + (
                    "检索索引已更新。"
                    if all(j["state"] == "complete" for j in jobs)
                    else "检索索引仍在更新。"
                )
            )
        )
    elif (
        failed_links
        or failed_jobs
        or any(x.get("reason") for x in value.get("automatic_imports", []))
    ):
        states = {automatic_jobs.display_state(j) for j in failed_jobs}
        stage = (
            "waiting_budget"
            if "waiting_budget" in states
            else "waiting_dependency"
            if "waiting_dependency" in states
            else "failed"
        )
        text = {
            "waiting_budget": "处理等待模型预算，尚未完成入库。",
            "waiting_dependency": "处理等待所需环境，尚未完成入库。",
            "failed": "提取或处理失败，本批尚未全部入库。",
        }[stage]
        text += "已保留收到的消息/附件及链接；远端正文或视频可能尚未取得。"
        if reasons:
            text += "\n" + "\n".join(reason_text(r) for r in reasons[:3])
    elif any(j["state"] == "cancelled" for j in jobs):
        stage, text = (
            "cancelled",
            "本批有任务已取消，其余任务按各自状态继续；"
            "原消息、已提交调用费用及已经入库的笔记保留。",
        )
    elif not pending and (
        any(j["state"] == "reviewed" for j in jobs)
        or any(x.get("state") == "review_required" for x in links)
    ):
        stage, text = "awaiting_review", "提取已完成，等待人工核验；审核通过后才保存到 Inbox。"
    elif not links and not jobs:
        stage, text = (
            "archived_only",
            "仅消息/附件已归档，尚未生成笔记；请在 Fiber bundle 核对处理策略。",
        )
    else:
        return None
    if any((x.get("metadata") or {}).get("transcript_route") == "visual_primary" for x in links):
        text += (
            "\n本批含画面主导笔记：依据关键帧文字与图表整理，ASR未取得有效文字，语音内容未核验。"
        )
    key = (
        stage
        + ":"
        + digest(
            canonical(
                {
                    "attempts": [x.get("attempt_id") for x in links],
                    "jobs": [j["id"] for j in jobs],
                    "reasons": reasons if stage != "saved" else [],
                }
            ).encode()
        )
    )
    return {
        "key": key,
        "stage": stage,
        "body": f"批次 {batch}\n{text}",
        "done": stage in {"saved", "archived_only"} and not pending,
    }
