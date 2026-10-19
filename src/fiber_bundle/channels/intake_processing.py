"""Observe new material batches; never replay history or retry ambiguous work."""

import json
import sqlite3
import time
from typing import Any

from .. import database, feishu_receiver, weixin_batches, weixin_inbox
from ..config import Config, InvalidInput
from ..inputs import canonical
from . import intake_classify, operations, store
from . import intake_repository as repository
from .services import policy as service_policy


def guard_locked(
    config: Config, identity: str, revision: int, *, manual: bool = False
) -> dict[str, Any]:
    with store.readonly(config) as db:
        if db is None:
            raise InvalidInput("intake_policy_unavailable")
        channel = operations.entry(config, db, identity)
        policy = repository.policy(db, identity)
        if (
            policy["revision"] != revision
            or (not policy["settings"]["enabled"] and not manual)
            or not service_policy.intake_allowed(config, channel)
            or policy["binding_sha"] != repository.fingerprint(channel)
            or (
                operations.pending(db, identity)
                and not (
                    service_policy.managed(config, identity)
                    and (operations.latest(db, identity) or {}).get("action") == "pause"
                )
            )
        ):
            raise InvalidInput("intake_policy_changed")
        main = database.connect(config.state_root)
        try:
            repository.authorize(config, main, channel, policy["settings"])
        finally:
            main.close()
        return policy


def record(
    db: sqlite3.Connection,
    identity: str,
    batch: str,
    revision: int,
    state: str,
    reason: str | None,
    value: dict[str, Any],
) -> None:
    now = time.time()
    db.execute(
        "INSERT INTO channel_intake_records VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(channel_id,batch_id) DO UPDATE SET revision=excluded.revision,"
        "state=excluded.state,reason=excluded.reason,value=excluded.value,updated=excluded.updated",
        (identity, batch, revision, state, reason, canonical(value), now, now),
    )


def prepare(config: Config, identity: str) -> int:
    channel, policy = repository.channel(config, identity)
    if not policy["settings"]["enabled"] or not service_policy.intake_allowed(config, channel):
        return 0
    bound = repository.binding(channel)
    service = service_policy.get(config, identity, "intake")
    if service:
        policy = {
            **policy,
            "after_row": max(policy["after_row"], service["source_watermark"]),
            "updated": max(policy["updated"], service["admit_after"]),
        }
    queued = 0
    with weixin_inbox.connection(config, bound) as journal, store.opened(config) as side:
        guard_locked(config, identity, policy["revision"])
        # Completed rows must not starve later batches. Only 100 actionable rows
        # are evaluated per tick, bounded by this policy's activation watermark.
        rows = journal.execute(
            "SELECT b.id FROM wx_batches b WHERE b.account_id=? AND b.state='frozen' "
            "AND EXISTS(SELECT 1 FROM wx_batch_members m JOIN events e "
            "ON m.account_id=e.account_id AND m.event_id=e.event_id "
            "WHERE m.batch_id=b.id AND e.rowid>?) ORDER BY b.rowid",
            (bound.account_id, policy["after_row"]),
        )
        examined = 0
        for row in rows:
            from ..intake_abort_store import requested

            if requested(config, {"channel": identity, "batch": row["id"]}):
                continue
            saved = side.execute(
                "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
                (identity, row["id"]),
            ).fetchone()
            if saved is not None and (
                saved["state"] != "waiting" or saved["revision"] != policy["revision"]
            ):
                continue
            ready = weixin_batches.snapshot(journal, bound, row["id"])["content_ready"]
            # An unchanged missing attachment must not consume the 100-row work
            # allowance forever and starve newer, complete messages.
            if saved is not None and not ready:
                continue
            value = intake_classify.classify(journal, bound, row["id"], policy["settings"])
            reason = value["reason"]
            if value["first_row"] <= policy["after_row"] or (
                value["oldest_sent_at"] is not None and value["oldest_sent_at"] < policy["updated"]
            ):
                reason = "intake_contains_historical_messages"
            state = "manual" if reason else "queued" if ready else "waiting"
            value["target_vault_id"] = policy["settings"]["target_vault_id"]
            if not ready and not reason:
                reason = "intake_attachments_missing"
            with database.transaction(side):
                record(side, identity, row["id"], policy["revision"], state, reason, value)
            queued += state == "queued"
            examined += 1
            if examined == 100:
                break
    return queued


def advance(config: Config, identity: str) -> int:
    from .intake_archive import archive

    prepare(config, identity)
    promote_manual(config, identity)
    channel, policy = repository.channel(config, identity)
    if not service_policy.intake_allowed(config, channel):
        return 0
    with store.readonly(config) as db:
        if db is None or not repository.existing(db):
            return 0
        row = db.execute(
            "SELECT * FROM channel_intake_records WHERE channel_id=? AND state='queued' "
            "AND created>=? ORDER BY created LIMIT 1",
            (identity, service_policy.cutoff(config, identity)),
        ).fetchone()
    if row is None:
        return 0
    manual = bool(json.loads(row["value"]).get("manual"))
    if not policy["settings"]["enabled"] and not manual:
        return 0
    archive(config, identity, row["batch_id"], row["revision"], manual=manual)
    return 1


def promote_manual(config: Config, identity: str) -> None:
    channel, policy = repository.channel(config, identity)
    with store.readonly(config) as side:
        if side is None or not repository.existing(side):
            return
        rows = list(
            side.execute(
                "SELECT * FROM channel_intake_records WHERE channel_id=? AND state='waiting' "
                "AND json_extract(value,'$.manual')=1 AND created>=? LIMIT 20",
                (identity, service_policy.cutoff(config, identity)),
            )
        )
    if not rows:
        return
    bound = repository.binding(channel)
    with weixin_inbox.connection(config, bound) as journal, store.opened(config) as side:
        guard_locked(config, identity, policy["revision"], manual=True)
        for row in rows:
            if (
                row["revision"] == policy["revision"]
                and weixin_batches.snapshot(journal, bound, row["batch_id"])["content_ready"]
            ):
                with database.transaction(side):
                    record(
                        side,
                        identity,
                        row["batch_id"],
                        policy["revision"],
                        "queued",
                        None,
                        json.loads(row["value"]),
                    )


def download_record(
    config: Config, identity: str, revision: int, journal: sqlite3.Connection, job: str
) -> dict[str, Any] | None:
    event = journal.execute(
        "SELECT rowid,event_id,account_id FROM events WHERE job_id=?", (job,)
    ).fetchone()
    if event is None:
        return None
    batches = journal.execute(
        "SELECT batch_id FROM wx_batch_members WHERE event_id=? AND account_id=?",
        (event["event_id"], event["account_id"]),
    ).fetchall()
    with store.readonly(config) as side:
        if side is None:
            return None
        for batch in batches:
            row = side.execute(
                "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=? "
                "AND revision=? AND state='waiting' AND created>=?",
                (identity, batch[0], revision, service_policy.cutoff(config, identity)),
            ).fetchone()
            if row:
                value: dict[str, Any] = json.loads(row["value"])
                policy = guard_locked(config, identity, revision, manual=bool(value.get("manual")))
                if (
                    not repository.unlimited(policy["settings"], "store")
                    and repository.consumed(side, identity)["store"]
                    >= policy["settings"]["store_limit"]
                ):
                    reserved = side.execute(
                        "SELECT 1 FROM channel_intake_consumption WHERE channel_id=? "
                        "AND kind='store' AND target=?",
                        (identity, batch[0]),
                    ).fetchone()
                    if reserved is None:
                        return None
                if value.get("manual") or event["rowid"] > policy["after_row"]:
                    return value
    return None


async def download(config: Config, identity: str, adapter: Any) -> bool:
    channel, policy = repository.channel(config, identity)
    if (
        not repository.unlimited(policy["settings"], "download")
        and not policy["settings"]["download_limit"]
    ):
        return False

    def reserve(journal: sqlite3.Connection, target: str) -> bool:
        value = download_record(
            config, identity, policy["revision"], journal, target.rsplit(":", 1)[0]
        )
        if value is None:
            return False
        with store.opened(config) as side, database.transaction(side):
            return repository.reserve(
                side,
                identity,
                policy["settings"],
                "download",
                target + ":" + value.get("retry_id", "initial"),
            )

    def checked() -> None:
        guard_locked(config, identity, policy["revision"], manual=True)

    return await feishu_receiver.download_one(
        config,
        repository.binding(channel),
        adapter,
        reserve=reserve,
        eligible=lambda db, job: (
            download_record(config, identity, policy["revision"], db, job) is not None
        ),
        guard=checked,
    )
