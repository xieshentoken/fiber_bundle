"""Bind downloaded, verified Feishu PDF bytes before freezing one immutable expert request."""

import json
import time
from collections.abc import Callable
from typing import Any

from . import database, source_import_existing, weixin_inbox, weixin_recovery
from . import expert_file_intents as intents
from .attachment_projection import epoch
from .config import Config, InvalidInput, StorageError
from .inputs import AssetInput, canonical
from .store import Store
from .weixin_events import Binding


def dispatch(
    config: Config,
    binding: Binding,
    authorization: str,
    *,
    guard: Callable[[], None] | None = None,
    minimum_rowid: int = 0,
) -> dict[str, Any] | None:
    selected = None
    with weixin_inbox.connection(config, binding) as journal:
        if guard:
            guard()
        weixin_inbox.bound(journal, binding)
        if not journal.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fiber_expert_commands'"
        ).fetchone():
            return None
        if minimum_rowid == 0:
            intents.expire(journal, time.time())
        row = journal.execute(
            (
                "SELECT * FROM fiber_expert_commands WHERE authorization=? AND sta"
                "te IN ('awaiting_download','bound') AND rowid>? ORDER BY rowid LIMIT 1"
            ),
            (authorization, minimum_rowid),
        ).fetchone()
        if row is None:
            return None
        value = json.loads(row["request_json"])
        intent = value.get("intent")
        if intent is None:
            return None
        main = database.connect(config.state_root)
        try:
            current_scope = {
                **binding.__dict__,
                "chat_id": intent["scope"]["chat_id"],
                "permission_epoch": epoch(main, binding.client_id, binding.vault_id),
            }
            if intent["scope"] != current_scope:
                raise InvalidInput("expert_feishu_scope_changed")
            chosen = intent.get("selected_file")
            if chosen is None:
                original = journal.execute(
                    "SELECT * FROM events WHERE account_id=? AND event_id=?",
                    (binding.account_id, intent["reply_message_id"]),
                ).fetchone()
                if original is None:
                    intent["reason"] = "expert_feishu_replied_source_not_archived"
                    journal.execute(
                        (
                            "UPDATE fiber_expert_commands SET request_json=? WHERE authorizati"
                            "on=? AND event_id=?"
                        ),
                        (canonical(value), authorization, row["event_id"]),
                    )
                    return {"state": "awaiting_download", "next_action": intent["reason"]}
                message = json.loads(original["raw"])
                raw = message["fiber_feishu"]["raw_event"]["event"]["message"]
                if (
                    not intents.direct(binding, message)
                    or raw["chat_id"] != intent["scope"]["chat_id"]
                ):
                    raise InvalidInput("expert_feishu_reply_scope_mismatch")
                candidates = [
                    {"event_id": original["event_id"], "item_key": key, **file}
                    for key, file in message["fiber_feishu"]["files"].items()
                ]
                intent["candidates"] = candidates
                if len(candidates) != 1:
                    journal.execute(
                        (
                            "UPDATE fiber_expert_commands SET state='needs_selection',request_"
                            "json=? WHERE authorization=? AND event_id=?"
                        ),
                        (canonical(value), authorization, row["event_id"]),
                    )
                    return {"state": "needs_selection", "candidates": candidates}
                chosen = intent["selected_file"] = candidates[0]
            event = journal.execute(
                "SELECT * FROM events WHERE account_id=? AND event_id=?",
                (binding.account_id, chosen["event_id"]),
            ).fetchone()
            if event is None:
                return None
            _, media, _, state = weixin_recovery.load(journal, binding, event["job_id"])
            if state != "pending" or chosen["item_key"] not in media:
                return None
            raw_bytes = media[chosen["item_key"]]
            if not chosen["name"].lower().endswith(".pdf") or not raw_bytes.startswith(b"%PDF-"):
                raise InvalidInput("expert_feishu_pdf_required")
            # CAS consumes the exact one-shot input before either archive or task side effect.
            changed = journal.execute(
                (
                    "UPDATE fiber_expert_commands SET state='bound',request_json=? WHE"
                    "RE authorization=? AND event_id=? AND state IN ('awaiting_downloa"
                    "d','bound')"
                ),
                (canonical(value), authorization, row["event_id"]),
            )
            if changed.rowcount != 1:
                return None
            from . import expert_source_reuse

            reused = expert_source_reuse.receipt(
                main,
                journal,
                event["job_id"],
                binding.client_id,
                binding.vault_id,
                chosen["name"],
                raw_bytes,
            )
            if reused is None:
                receipt = Store(config).ingest_locked(
                    main,
                    ingress_scope="expert-feishu:" + binding.account_id,
                    event_id=chosen["event_id"] + ":" + chosen["item_key"],
                    vault=main.execute(
                        "SELECT name FROM vaults WHERE id=?", (binding.vault_id,)
                    ).fetchone()[0],
                    source_key="expert-feishu:" + chosen["event_id"] + ":" + chosen["item_key"],
                    raw_event=canonical(
                        {"command_event": row["event_id"], "intent": intent}
                    ).encode(),
                    items=(AssetInput(raw_bytes, chosen["name"], "application/pdf", "body"),),
                    session_scope="expert-feishu:" + binding.account_id,
                    session_id=chosen["event_id"],
                    retention="knowledge",
                    owner_client_id=binding.client_id,
                )
                if receipt.state != "stored":
                    raise InvalidInput("expert_feishu_source_incomplete")
                asset = main.execute(
                    "SELECT asset_id FROM version_assets WHERE version_id=? AND role='body'",
                    (receipt.source_version_id,),
                ).fetchone()[0]
                receipt_id = receipt.job_id
            else:
                receipt_id, asset = reused
            from .attachment_projection import archive_version

            version_id = main.execute(
                "SELECT source_version_id FROM receipts WHERE job_id=?", (receipt_id,)
            ).fetchone()[0]
            archive_version(config, main, binding.client_id, binding.vault_id, version_id)
            with database.transaction(main):
                value["intent"]["ordinary_requests"] = expert_source_reuse.stop_unstarted(
                    config, main, binding.client_id, receipt_id, asset
                )
                selected = source_import_existing.create(
                    config,
                    main,
                    binding.client_id,
                    binding.vault_id,
                    receipt_id,
                    asset,
                    "fake-expert",
                    {
                        "command_event": row["event_id"],
                        "scope": intent["scope"],
                        "file_message_id": chosen["event_id"],
                    },
                )
            value["selected"] = {"action": "start", "id": selected["id"], "workpack": None}
            value["intent"]["source_sha256"] = selected["source_sha256"]
            value["intent"]["source_bytes"] = selected["source_bytes"]
            journal.execute(
                (
                    "UPDATE fiber_expert_commands SET state='queued',task_id=?,request"
                    "_json=? WHERE authorization=? AND event_id=? AND state='bound'"
                ),
                (selected["id"], canonical(value), authorization, row["event_id"]),
            )
        except (StorageError, OSError):
            journal.execute(
                (
                    "UPDATE fiber_expert_commands SET state='failed' WHERE authorizati"
                    "on=? AND event_id=?"
                ),
                (authorization, row["event_id"]),
            )
            raise
        finally:
            main.close()
    return {"state": "queued", "task_id": selected["id"]} if selected else None
