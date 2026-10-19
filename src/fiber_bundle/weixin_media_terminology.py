"""Task-local exact owner terminology commands; no processing or implicit delivery."""

import json
import re
import secrets
import sqlite3
from contextlib import closing
from typing import Any

from . import auth, database, media_glossary
from . import weixin_inbox as inbox
from .config import Config, IntegrityError, InvalidInput
from .media_glossary_contract import ambiguities
from .weixin_events import Binding

COMMAND = re.compile(r"术语 ([0-9a-f]{16}) (确认|确认空表) ([1-9][0-9]{0,2}) ([0-9a-f]{64})")
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS wx_media_terminology_prompts("
    "id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES bindings(account_id),"
    "job_id TEXT NOT NULL,revision INTEGER NOT NULL,sha256 TEXT NOT NULL,"
    "body TEXT NOT NULL,state TEXT NOT NULL "
    "CHECK(state IN ('pending','sending','sent','unknown','obsolete')),"
    "UNIQUE(account_id,job_id,revision)) STRICT"
)


def initialize(config: Config, binding: Binding) -> None:
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        db.execute(SCHEMA)


def view(config: Config, main: sqlite3.Connection, binding: Binding, job_id: str) -> sqlite3.Row:
    job = media_glossary.access(config, main, binding.client_id, job_id)
    if job["vault_id"] != binding.vault_id:
        raise InvalidInput("media_terminology_vault_scope")
    # Exporting the frozen topic/terms is a separate query permission.
    auth.grant(main, binding.client_id, binding.vault_id, "query")
    media_glossary.mutable(job)
    row = media_glossary.latest(main, job_id)
    if row is None:
        raise InvalidInput("media_terminology_snapshot_required")
    media_glossary.verified(row)
    return row


def prepare(config: Config, binding: Binding, job_id: str) -> dict[str, Any]:
    initialize(config, binding)
    with (
        inbox.connection(config, binding) as db,
        closing(database.connect(config.state_root)) as main,
    ):
        inbox.bound(db, binding)
        row = view(config, main, binding, job_id)
        saved = db.execute(
            "SELECT * FROM wx_media_terminology_prompts "
            "WHERE account_id=? AND job_id=? AND revision=?",
            (binding.account_id, job_id, row["revision"]),
        ).fetchone()
        if saved is not None:
            if saved["sha256"] != row["snapshot_sha256"]:
                raise IntegrityError("media_terminology_prompt_changed")
            return {"prompt_id": saved["id"], "text": saved["body"], "state": saved["state"]}
        snapshot = media_glossary.verified(row)
        nonce = secrets.token_hex(8)
        body = f"媒体任务 {job_id}\n术语修订 {row['revision']} SHA {row['snapshot_sha256']}\n"
        body += json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        if ambiguities(snapshot):
            body += "\n存在未决歧义，须先修订术语表；本修订不能确认。"
        else:
            label = "确认" if snapshot["terms"] else "确认空表"
            body += (
                f"\n逐项核对后，完整回复：术语 {nonce} {label} "
                f"{row['revision']} {row['snapshot_sha256']}"
            )
        body += "\n确认只记录术语认可，不执行转录、发布或清理；不会自动提醒重发。"
        if len(body.encode("utf-8")) > 6000:
            raise InvalidInput("media_terminology_prompt_size")
        db.execute(
            "INSERT INTO wx_media_terminology_prompts VALUES(?,?,?,?,?,?,'pending')",
            (nonce, binding.account_id, job_id, row["revision"], row["snapshot_sha256"], body),
        )
        return {"prompt_id": nonce, "text": body, "state": "pending"}


def checked(
    config: Config, main: sqlite3.Connection, binding: Binding, prompt: sqlite3.Row
) -> None:
    row = view(config, main, binding, prompt["job_id"])
    if (row["revision"], row["snapshot_sha256"]) != (prompt["revision"], prompt["sha256"]):
        raise InvalidInput("media_terminology_prompt_stale")


def apply_locked(
    config: Config, db: sqlite3.Connection, binding: Binding, value: str
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": "rejected",
        "error_code": "invalid_media_terminology_command",
    }
    match = COMMAND.fullmatch(value)
    if (
        match is None
        or not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='wx_media_terminology_prompts'"
        ).fetchone()
    ):
        return result
    nonce, label, revision, sha = match.groups()
    prompt = db.execute(
        "SELECT * FROM wx_media_terminology_prompts WHERE account_id=? AND id=?",
        (binding.account_id, nonce),
    ).fetchone()
    if prompt is None or (prompt["revision"], prompt["sha256"]) != (int(revision), sha):
        return result
    try:
        with closing(database.connect(config.state_root)) as main:
            checked(config, main, binding, prompt)
            gate = media_glossary.confirm_locked(
                config,
                main,
                binding.client_id,
                prompt["job_id"],
                int(revision),
                sha,
                allow_empty=label == "确认空表",
            )
        db.execute(
            "UPDATE wx_media_terminology_prompts SET state='obsolete' "
            "WHERE id=? AND state='pending'",
            (nonce,),
        )
        return {
            "state": "accepted",
            "terminology": gate,
            "reply_text": "该修订术语认可已记录，转录、发布和清理尚未执行。",
        }
    except (InvalidInput, IntegrityError, auth.Denied):
        result["error_code"] = "media_terminology_confirmation_rejected"
        return result
