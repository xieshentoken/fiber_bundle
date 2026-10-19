"""Exact owner document intents; journal caller already holds the main storage lock."""

from typing import Any

from . import auth, database
from . import document_creation_binding as execution
from . import document_creation_continuation as continuation
from . import document_creation_delivery as delivery
from . import document_creation_save as save
from . import document_creation_tasks as tasks
from .config import Config, IntegrityError, InvalidInput
from .weixin_events import Binding


def apply_locked(config: Config, binding: Binding, event_id: str, value: str) -> dict[str, Any]:
    """Commands queue intent only. Model/file dispatch belongs to the finite host worker."""
    rejected = {"state": "rejected", "error_code": "document_command_rejected"}
    header, _, body = value.partition("\n")
    parts = header.split(" ")
    if len(parts) < 3 or parts[0] != "文档" or any(not v for v in parts):
        return rejected
    _, action, *args = parts
    db = database.connect(config.state_root)
    try:
        client = binding.client_id
        if action == "创建" and len(args) == 2 and body.strip():
            query, fmt = args
            result = tasks.submit(config, db, client, "weixin:" + event_id, query, body, fmt)
        elif action == "候选" and len(args) == 1 and not body:
            result = continuation.candidates(config, db, client, args[0])
        elif action == "状态" and len(args) == 1 and not body:
            result = tasks.status(config, db, client, args[0])
            if execution.get(db, args[0]) is not None:
                result["file_delivery"] = delivery.status(db, client, args[0])
        elif action == "输入预览" and len(args) == 2 and not body:
            result = continuation.update_preview(config, db, client, args[0], args[1])
        elif (
            action in {"续改", "更新"}
            and len(args) == (3 if action == "续改" else 5)
            and body.strip()
        ):
            task_id, revision, sha = args[:3]
            if execution.get(db, task_id) is None:
                if action == "更新":
                    raise InvalidInput("document_update_requires_bound_parent")
                result = tasks.amend(
                    config,
                    db,
                    client,
                    task_id,
                    body,
                    expected_revision=int(revision),
                    expected_sha256=sha,
                )
            else:
                result = continuation.enqueue(
                    config,
                    db,
                    client,
                    task_id,
                    "weixin:" + event_id,
                    body,
                    expected_revision=int(revision),
                    expected_sha256=sha,
                    updated_query_task_id=args[3] if action == "更新" else None,
                    expected_update_sha256=args[4] if action == "更新" else None,
                )
        elif action == "取消" and len(args) == 3 and not body:
            task_id, revision, sha = args
            head = tasks.status(config, db, client, task_id)
            if (head["revision"], head["plan_sha256"]) != (int(revision), sha):
                raise InvalidInput("document_plan_conflict")
            for child in reversed(continuation.versions(config, db, client, task_id)):
                tasks.cancel(
                    config,
                    db,
                    client,
                    child["task_id"],
                    expected_revision=child["revision"],
                    expected_sha256=child["plan_sha256"],
                )
            result = tasks.status(config, db, client, task_id)
        elif action == "保存" and len(args) == 3 and not body:
            task_id, vault, sha = args
            result = save.prepare(
                config, db, client, task_id, vault, expected_artifact_sha256=sha, allow_save=True
            )
            if result["state"] != "scope_approval_required":
                result = save.publish(config, db, client, task_id, allow_save=True)
        else:
            return rejected
        return {
            "state": "accepted",
            "document": result,
            "reply_text": "文档请求已记录；草稿未核验，执行与文件交付状态须分别查看。",
        }
    except (InvalidInput, IntegrityError, auth.Denied, ValueError):
        return rejected
    finally:
        db.close()
