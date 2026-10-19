"""Admin-selected history/recovery and read-only status, separate from scheduling."""

import json
from typing import Any
from uuid import uuid4

from .. import admin_batches, auth, database, weixin_batches, weixin_inbox
from ..config import Config, InvalidInput
from ..weixin_store_dispatch import worker_lock
from . import intake_classify, intake_contract, operations, store
from . import intake_repository as repository
from .intake_processing import guard_locked, record


def inspect(config: Config, identity: str) -> dict[str, Any]:
    channel, policy = repository.channel(config, identity)
    blockers = []
    main = database.connect(config.state_root)
    try:
        targets = [
            dict(row)
            for row in main.execute(
                "SELECT v.id,v.name FROM vaults v JOIN client_vault_grants g ON g.vault_id=v.id "
                "WHERE g.client_id=? AND g.can_add=1 AND v.read_only=0 AND v.status='active' "
                "AND v.attachment_state='attached'",
                (channel["value"]["client_id"],),
            )
        ]
        if channel["value"].get("platform", "feishu") == "feishu":
            try:
                repository.authorize(config, main, channel, policy["settings"])
                from .intake_health import admission_reason

                reason = admission_reason(config, main, channel, policy["settings"])
                if reason:
                    blockers.append(reason)
            except auth.Denied:
                blockers.append("intake_permission_unavailable")
            except InvalidInput as error:
                code = str(error)
                blockers.append(code if code.startswith("intake_") else "intake_target_unavailable")
    finally:
        main.close()
    with store.readonly(config) as db:
        view = repository.present(db, channel)
        rows = (
            list(
                db.execute(
                    "SELECT * FROM channel_intake_records WHERE channel_id=? "
                    "ORDER BY created DESC LIMIT 50",
                    (identity,),
                )
            )
            if db is not None and repository.existing(db)
            else []
        )
        records = [{**dict(row), "value": json.loads(row["value"])} for row in rows]
    if channel["state"] != "running":
        blockers.append("intake_channel_paused")
    if not view["binding_current"]:
        blockers.append("intake_binding_changed")
    if (
        view["settings"]["enabled"]
        and not repository.unlimited(view["settings"], "store")
        and not view["remaining"]["store"]
    ):
        blockers.append("intake_store_budget_exhausted")
    history = []
    if view["supported"]:
        bound = repository.binding(channel)
        with admin_batches.journal(config) as journal:
            if (
                journal is not None
                and journal.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='wx_batches'"
                ).fetchone()
            ):
                history = [
                    dict(r)
                    for r in journal.execute(
                        "SELECT id AS batch_id,state,last_received FROM wx_batches "
                        "WHERE account_id=? AND state='frozen' ORDER BY rowid DESC LIMIT 50",
                        (bound.account_id,),
                    )
                ]
                if journal.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='fiber_feishu_downloads'"
                ).fetchone():
                    for item in records:
                        if item["state"] != "waiting":
                            continue
                        states = {
                            row[0]
                            for row in journal.execute(
                                "SELECT d.state FROM fiber_feishu_downloads d "
                                "JOIN events e ON e.job_id=d.job_id JOIN wx_batch_members m "
                                "ON e.account_id=m.account_id AND e.event_id=m.event_id "
                                "WHERE m.batch_id=?",
                                (item["batch_id"],),
                            )
                        }
                        item["reason"] = (
                            "intake_download_requires_reconciliation"
                            if "running" in states
                            else "intake_attachment_download_failed"
                            if "failed" in states
                            else "intake_store_budget_exhausted"
                            if not view["remaining"]["store"]
                            else "intake_download_budget_exhausted"
                            if not view["remaining"]["download"]
                            else item["reason"]
                        )
    from .batch_intake import worker_status
    from .intake_outcomes import outcome, reason_text
    from .intake_recovery_status import project as recovery_status

    with database.connect(config.state_root) as main:
        for item in records:
            item["outcome"] = outcome(main, channel, item["batch_id"], item["value"])
            for link in item["value"].get("links", []):
                link.update(recovery_status(main, link))
                link["error_message"] = reason_text(link["reason"]) if link.get("reason") else None
    return {
        **view,
        "worker": worker_status(config, channel),
        "sites": intake_contract.SITES,
        "targets": targets,
        "records": records,
        "history": history,
        "blockers": blockers,
    }


def select(
    config: Config,
    identity: str,
    data: intake_contract.BatchAction,
    *,
    leased: bool = False,
    choice: str | None = None,
) -> dict[str, Any]:
    if choice not in {None, "save_one", "save_separately"}:
        raise InvalidInput("intake_batch_changed")
    channel, _ = repository.channel(config, identity)
    bound = repository.binding(channel)
    with (
        worker_lock(config),
        weixin_inbox.connection(config, bound, leased=leased) as journal,
        store.opened(config) as side,
    ):
        policy = guard_locked(config, identity, data.expected_revision, manual=True)
        planned = []
        for batch in dict.fromkeys(data.batch_ids):
            view = weixin_batches.snapshot(journal, bound, batch)
            saved = side.execute(
                "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
                (identity, batch),
            ).fetchone()
            if saved is not None and saved["state"] == "stored":
                continue
            if (
                saved is not None
                and saved["state"] in {"queued", "waiting"}
                and json.loads(saved["value"]).get("choice") == choice
            ):
                continue
            value = intake_classify.classify(journal, bound, batch, policy["settings"])
            if value["reason"] in {"intake_command_requires_routing", "intake_message_unsupported"}:
                raise InvalidInput(value["reason"])
            recovering = saved is not None and saved["state"] in {"archiving", "needs_review"}
            selected = (
                choice is not None and view["state"] == "selected" and view["action"] == choice
            )
            if view["state"] != "frozen" and not (
                (recovering or selected) and view["state"] == "selected"
            ):
                raise InvalidInput("intake_batch_changed")
            handoff = (
                journal.execute(
                    "SELECT state FROM wx_store_handoffs WHERE batch_id=?", (batch,)
                ).fetchone()
                if journal.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='wx_store_handoffs'"
                ).fetchone()
                else None
            )
            if handoff and saved is None:
                if handoff["state"] == "stored":
                    continue
                raise InvalidInput("intake_archive_interrupted")
            if saved is not None:
                old = json.loads(saved["value"])
                if recovering and old["target_vault_id"] != policy["settings"]["target_vault_id"]:
                    raise InvalidInput("intake_target_changed")
                if recovering:
                    from .intake_failures import restore

                    restore(value, old, saved["reason"])
            value.update(
                manual=True,
                recovering=recovering,
                target_vault_id=policy["settings"]["target_vault_id"],
                retry_id=str(uuid4()),
                choice=choice or (view.get("action") if recovering else None),
            )
            if not view["content_ready"]:
                # Only known failed resource attempts may be retried; running/unknown stays held.
                if journal.execute(
                    "SELECT 1 FROM fiber_feishu_downloads d JOIN wx_batch_members m "
                    "JOIN events e ON e.job_id=d.job_id AND e.account_id=m.account_id "
                    "AND e.event_id=m.event_id WHERE m.batch_id=? AND d.state='running'",
                    (batch,),
                ).fetchone():
                    raise InvalidInput("intake_download_requires_reconciliation")
            planned.append((batch, "queued" if view["content_ready"] else "waiting", value))
        with database.transaction(side):
            for batch, state, value in planned:
                record(
                    side,
                    identity,
                    batch,
                    policy["revision"],
                    state,
                    None if state == "queued" else "intake_attachments_missing",
                    value,
                )
        with database.transaction(journal):
            for batch, _, _ in planned:
                journal.execute(
                    "DELETE FROM fiber_feishu_downloads WHERE state='failed' AND job_id IN "
                    "(SELECT e.job_id FROM wx_batch_members m JOIN events e "
                    "ON e.account_id=m.account_id AND e.event_id=m.event_id WHERE m.batch_id=?)",
                    (batch,),
                )
    return {"queued": len(planned), "executed": False}


def cancel(
    config: Config, identity: str, data: intake_contract.BatchAction, *, leased: bool = False
) -> dict[str, Any]:
    with repository.lease(config, leased), store.opened(config) as db, database.transaction(db):
        operations.entry(config, db, identity)
        if repository.policy(db, identity)["revision"] != data.expected_revision:
            raise InvalidInput("intake_revision_conflict")
        changed = 0
        for batch in dict.fromkeys(data.batch_ids):
            changed += db.execute(
                "UPDATE channel_intake_records SET state='manual',reason='intake_user_cancelled' "
                "WHERE channel_id=? AND batch_id=? AND state IN ('waiting','queued')",
                (identity, batch),
            ).rowcount
    return {"cancelled": changed, "originals_deleted": False}
