"""Owner-only Feishu organization commands, separate from material intake and query."""

import json
import sqlite3
import time
from pathlib import PurePosixPath
from typing import Any

from .. import auth, database, feishu_sync
from ..config import Config, IntegrityError, InvalidInput
from ..inputs import canonical
from ..objects import digest
from . import jobs, membership, packages, processing, store
from .contracts import ORGANIZER_VERSION

HELP = (
    "发送 /归类，对当前知识库中尚未分类的笔记和专家包进行增量归类。"
    "保留人工关系；无法可靠归类的项目在知识关联页复核。"
)


def parse(text: str) -> Any:
    if not text.startswith("/归类"):
        return None
    tail = text.removeprefix("/归类").strip()
    for word, action in [
        ("预览", "preview"),
        ("确认", "confirm"),
        ("状态", "status"),
        ("取消", "cancel"),
        ("恢复", "resume"),
        ("移除", "unlink"),
        ("解锁", "unlock"),
    ]:
        if tail.startswith(word + " "):
            return {"action": action, "target": tail[len(word) :].strip()}
    if not tail:
        return {"action": "run", "target": "待分类"}
    if " 到 MOC:" in tail:
        entity, moc = tail.split(" 到 MOC:", 1)
        return {"action": "link", "target": entity.strip(), "moc": moc.strip()}
    return {"action": "run", "target": tail}


def capture(config: Config, binding: Any, message: Any, guard: Any = None) -> bool:
    if message.get("forwarded") or message.get("from_user_id") != binding.owner_id:
        return False
    raw = message.get("fiber_feishu", {}).get("raw_event", {}).get("event", {}).get("message", {})
    if raw.get("message_type") != "text" or raw.get("chat_type") != "p2p":
        return False
    text = json.loads(raw.get("content", "{}")).get("text", "")
    command = parse(text) if isinstance(text, str) else None
    if command is None:
        return False
    with database.locked(config), database.connect(config.state_root) as db:
        if guard:
            guard()
        from ..vault_relocation import guard as relocation

        if relocation.metadata_only(binding.vault_id) and relocation.operation(
            db, binding.vault_id
        ):
            # This path only admits a durable command; execution rechecks the physical root.
            from ..agent_connections.visibility import scope

            scope(config, db, binding.client_id, [binding.vault_id])
            auth.grant(db, binding.client_id, binding.vault_id, "query")
            auth.grant(db, binding.client_id, binding.vault_id, "add")
        else:
            packages.context(config, db, binding.client_id, binding.vault_id, write=True)
        identity = digest(canonical([binding.__dict__, message["message_id"]]).encode())
        prior = store.get(config, "commands", identity)
        sha = digest(canonical(message).encode())
        if prior:
            if prior["sha256"] != sha:
                raise IntegrityError("organization_command_changed")
            return True
        store.put(
            config,
            "commands",
            identity,
            binding.vault_id,
            {
                "id": identity,
                "binding": binding.__dict__,
                "sha256": sha,
                "command": command,
                "parent": raw.get("parent_id"),
                "state": "queued",
                "created_at": time.time(),
                "receipt_state": None,
                "stage": "received",
                "response": "归类指令已受理。",
                "job_id": None,
            },
        )
    return True


def resolve(values: Any, target: str, kind: str) -> Any:
    target = target.removeprefix("专家包:") if kind == "entity" else target.removeprefix("MOC:")
    if kind == "entity" and target.startswith("笔记:"):
        target = target[3:]
    variants = {target}
    if kind == "entity" and not target.startswith(("expert:", "note:", "publication:")):
        variants.update({"expert:" + target, "note:" + target, "publication:" + target})
    matches = [
        v
        for v in values
        if variants & {v["id"], v["path"], v["title"], PurePosixPath(v["path"]).stem}
    ]
    if len(matches) != 1:
        raise InvalidInput("归类目标不存在或同名，请使用完整路径或ID。")
    return matches[0]


def response(job: Any) -> str:
    names = {
        "queued": "已排队",
        "running": "处理中",
        "preview": "预览待确认",
        "completed": "已完成",
        "partial": "部分完成",
        "failed": "失败",
        "cancelled": "已取消",
        "waiting_save": "等待保存",
    }
    items = job["items"]
    changed = sum(i.get("changed", 0) for i in items)
    review = sum(
        i["state"] in {"done", "suggested"} and i["status"] in {"review", "unclassified"}
        for i in items
    )
    blocked = sum(i["state"] == "blocked" for i in items)
    failures = [i.get("error", "未知错误") for i in items if i["state"] in {"failed", "blocked"}]
    preview = (
        "\n候选："
        + "；".join(i["title"] + " → " + (",".join(i["selected"]) or "待复核") for i in items[:8])
        if job["state"] == "preview"
        else ""
    )
    confirm = "\n确认执行：/归类 确认 " + job["id"] if job["state"] == "preview" else ""
    return (
        f"归类{names.get(job['state'], job['state'])}：{job['completed']}/{len(items)}，"
        f"新增关系 {changed}，待复核 {review}，暂停 {blocked}，"
        f"失败 {sum(i['state'] == 'failed' for i in items)}。\n任务 {job['id']}"
        + preview
        + confirm
        + ("\n原因：" + "；".join(failures[:3]) if failures else "")
        + ("\n" + str(job["error"]) if job.get("error") else "")
    )


def _cited_batch(config: Config, binding: Any, parent: Any) -> Any:
    if not parent:
        raise InvalidInput("请引用入库回执，或明确指定笔记/专家包ID。")
    from ..channels import store as channels

    target = digest(parent.encode())
    with channels.readonly(config) as side:
        if side is None:
            raise InvalidInput("尚无可关联回执。")
        if not side.execute(
            "SELECT 1 FROM sqlite_master WHERE name='channel_intake_records'"
        ).fetchone():
            raise InvalidInput("尚无可关联回执。")
        from ..channels import intake_repository, operations

        for row in side.execute("SELECT channel_id,batch_id,value FROM channel_intake_records"):
            channel = operations.entry(config, side, row["channel_id"])
            if intake_repository.binding(channel) != binding:
                continue
            value = json.loads(row["value"])
            if any(
                u.get("message_id_sha256") == target
                for u in value.get("receipt_updates", {}).values()
            ):
                return row["batch_id"]
    from .. import weixin_inbox

    with weixin_inbox.connection(config, binding) as journal:
        if journal.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fiber_feishu_delivery_evidence'"
        ).fetchone():
            found = journal.execute(
                "SELECT batch_id FROM fiber_feishu_delivery_evidence WHERE message_id_sha256=?",
                (target,),
            ).fetchone()
            if found:
                return found[0]
    raise InvalidInput("旧回执未保留可验证关联；请使用笔记或任务ID，不能猜测最近一条。")


def _batch_targets(
    config: Config, db: sqlite3.Connection, binding: Any, batch: int, entities: Any
) -> Any:
    rows = db.execute(
        "SELECT value,state,phase FROM automatic_jobs WHERE client_id=? AND vault_id=? "
        "AND (json_extract(value,'$.intake.batch')=? "
        "OR json_extract(value,'$.channel_batch.batch')=?)",
        (binding.client_id, binding.vault_id, batch, batch),
    ).fetchall()
    paths = []
    for r in rows:
        value = json.loads(r["value"])
        publication = value.get("publication") or {}
        if isinstance(publication, dict):
            for field in ["entry_note_path", "note_path", "relative_path"]:
                if publication.get(field):
                    paths.append(publication[field])
    matches = [
        e for e in entities if any(e["path"] == p or e["path"] == p + "/note.md" for p in paths)
    ]
    if len(matches) == 1:
        return matches[0]["id"]
    if rows and not paths:
        if any(r["state"] in {"queued", "claimed", "waiting"} for r in rows):
            return None
        raise InvalidInput("资料保存失败或尚待审核，请先在收件箱处理后再归类。")
    raise InvalidInput("此批次没有唯一可归类笔记，请明确指定目标。")


def execute(
    config: Config,
    db: sqlite3.Connection,
    binding: Any,
    command: Any,
    *,
    key: Any = None,
    parent: Any = None,
) -> Any:
    client, vault = binding.client_id, binding.vault_id
    packages.context(config, db, client, vault, write=True)
    action = command["action"]
    target = command["target"]
    if action == "help":
        return None, HELP
    if action in {"status", "cancel", "resume", "confirm"}:
        job = (
            jobs.get(config, db, client, vault, target)
            if action == "status"
            else jobs.action(config, db, client, vault, target, action)
        )
        return job["id"], response(job)
    if action in {"run", "preview"} and target in {"Inbox", "待分类", "全库"}:
        if target == "全库" and action != "preview":
            raise InvalidInput("全库历史归类请先发送 /归类 预览 全库，再确认预览任务。")
        if action == "run" and target == "待分类":
            for active in store.rows(config, "jobs", vault):
                if (
                    active["client_id"] == client
                    and active["target"] == target
                    and active["source"] == "command"
                    and active["state"] in {"queued", "running"}
                ):
                    return active["id"], "已有归类任务，继续处理当前任务。\n" + response(active)
        if action == "run" and target == "待分类":
            for prior in reversed(store.rows(config, "jobs", vault)):
                if (
                    prior["client_id"] == client
                    and prior["target"] == target
                    and prior["state"] == "partial"
                    and prior.get("organizer_version") == ORGANIZER_VERSION
                    and any(i["state"] in {"failed", "blocked"} for i in prior["items"])
                ):
                    if not processing.unchanged(config, db, prior):
                        continue
                    resumed = jobs.action(config, db, client, vault, prior["id"], "resume")
                    return resumed["id"], "继续上次暂停的归类。\n" + response(resumed)
        job = jobs.submit(
            config,
            db,
            client,
            vault,
            target,
            preview=action == "preview",
            key=key,
            source="command",
        )
        return job["id"], response(job)
    entities, mocs, _ = jobs.snapshot(
        config, db, client, vault, include=[] if target.startswith("专家包:") else None
    )
    if action in {"unlink", "unlock"}:
        if " 到 MOC:" not in target:
            raise InvalidInput(HELP)
        target, mid = target.split(" 到 MOC:", 1)
        command = {**command, "moc": mid}
    if action in {"link", "unlink", "unlock"}:
        entity = resolve(entities, target, "entity")
        moc = resolve(mocs, command["moc"], "moc")
        changed = membership.apply(
            config, db, client, vault, entity, moc, owner="manual", action=action
        )
        return (
            None,
            f"人工归类已记录：{entity['title']} → {moc['title']}；"
            f"关系{'已更新' if changed else '无需变更'}。",
        )
    if target == "此条":
        target = _batch_targets(
            config, db, binding, _cited_batch(config, binding, parent), entities
        )
        if target is None:
            return (
                "waiting-save",
                "资料尚未保存，归类等待保存完成；保存完成后将继续归类。",
            )
    if target not in {"Inbox", "待分类", "全库"}:
        target = resolve(entities, target, "entity")["path"]
    if target == "全库" and action != "preview":
        raise InvalidInput("全库历史归类请先发送 /归类 预览 全库，再确认预览任务。")
    job = jobs.submit(
        config, db, client, vault, target, preview=action == "preview", key=key, source="command"
    )
    return job["id"], response(job)


def advance(config: Config, binding: Any, guard: Any = None) -> Any:
    with database.locked(config), database.connect(config.state_root) as db:
        for row in store.rows(config, "commands", binding.vault_id):
            if row["binding"] != binding.__dict__:
                continue
            if row["state"] in {"queued", "waiting_save"}:
                if row["state"] == "waiting_save" and time.time() - row.get("last_check", 0) < 5:
                    continue
                if guard:
                    guard()
                try:
                    jid, body = execute(
                        config,
                        db,
                        binding,
                        row["command"],
                        key="command:" + row["id"],
                        parent=row["parent"],
                    )
                    if jid == "waiting-save":
                        row.update(state="waiting_save", last_check=time.time())
                        if row["stage"] != "waiting_save":
                            row.update(response=body, receipt_state=None, stage="waiting_save")
                        store.put(config, "commands", row["id"], binding.vault_id, row)
                        return
                    row.update(
                        state="watching" if jid else "done",
                        job_id=jid,
                        response=body,
                        receipt_state=None,
                        stage="accepted",
                    )
                except Exception as error:
                    row.update(
                        state="failed",
                        response="归类失败："
                        + (
                            str(error)[:300]
                            if isinstance(error, (InvalidInput, IntegrityError, auth.Denied))
                            else type(error).__name__
                        ),
                        receipt_state=None,
                        stage="failed",
                    )
                store.put(config, "commands", row["id"], binding.vault_id, row)
                return
            if row["state"] == "watching" and row.get("receipt_state") == "sent":
                job = jobs.get(config, db, binding.client_id, binding.vault_id, row["job_id"])
                if job["state"] in {"completed", "partial", "failed", "cancelled"}:
                    row.update(
                        state="done", response=response(job), stage="result", receipt_state=None
                    )
                    store.put(config, "commands", row["id"], binding.vault_id, row)
                    return


async def deliver(config: Config, binding: Any, sender: Any, guard: Any) -> Any:
    def take() -> Any:
        with database.locked(config), database.connect(config.state_root) as db:
            packages.context(config, db, binding.client_id, binding.vault_id, write=True)
            for row in store.rows(config, "commands", binding.vault_id):
                if (
                    row["binding"] == binding.__dict__
                    and row["receipt_state"] is None
                    and row["state"] != "queued"
                ):
                    row["receipt_state"] = "sending"
                    store.put(config, "commands", row["id"], binding.vault_id, row)
                    return row
        return None

    try:
        row = await feishu_sync.run(take)
    except database.LockTimeout:
        # Only the pre-send claim can wait for another receiver tick.
        return
    if row is None:
        return
    state = "unknown"
    try:
        state = "sent" if await sender(binding.owner_id, row["response"], guard) else "unknown"
    finally:

        def settle() -> Any:
            with database.locked(config):
                row["receipt_state"] = state
                store.put(config, "commands", row["id"], binding.vault_id, row)

        await feishu_sync.run(settle)
