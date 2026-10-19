"""Explicit confirmed-batch handoff to Store; recoverable across two durable databases."""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from . import auth, database, weixin_note_queries
from . import weixin_batches as batches
from . import weixin_inbox as inbox
from . import weixin_intents as intents
from . import weixin_store_inputs as projection
from .config import Config, IntegrityError, InvalidInput
from .inputs import canonical
from .objects import digest
from .store import Store
from .weixin_events import Binding
from .weixin_intent_text import retrieval_query


def transfer(
    config: Config,
    binding: Binding,
    batch_id: str,
    *,
    guard: Callable[[], None] | None = None,
    target_vault_id: str | None = None,
    leased: bool = False,
) -> dict[str, Any]:
    """Trusted local operation. No model, publication, message sending or automatic retry.

    inbox.connection holds the common storage lock throughout validation and transfer.
    The journal plan commits before Store writes. Stable event identities recover a
    crash after Store commit but before progress is recorded, without duplicate sources.
    """
    with inbox.connection(config, binding, leased=leased) as journal:
        inbox.bound(journal, binding)
        if guard is not None:
            guard()
        view = batches.snapshot(journal, binding, batch_id)
        if view["state"] != "selected" or not view["content_ready"] or view["needs_question"]:
            raise InvalidInput(
                "handoff requires a complete selected batch and consultation question"
            )
        action = view["action"]
        if action not in {"save_one", "save_separately", "consult"}:
            raise InvalidInput("invalid handoff action")
        if target_vault_id is not None and action == "consult":
            raise InvalidInput("target override only supports archival")
        question = journal.execute(
            "SELECT question FROM wx_batches WHERE id=?", (batch_id,)
        ).fetchone()[0]
        intent = intents.read_locked(journal, binding, batch_id)
        query_after_save = intents.query_after_save(intent, action, question)
        query_existing = weixin_note_queries.existing(intent)
        sources = [
            projection.source(journal, binding, member["job_id"], index)
            for index, member in enumerate(view["members"])
        ]
        # All originals and recovered bytes are checked before the first Store write.
        materials = [entry for entry in sources if entry[1]]
        groups = [[entry] for entry in materials] if action == "save_separately" else [sources]
        if query_existing:
            groups = []  # Question remains in the journal; never create source evidence for it.
        retention = "session" if action == "consult" else "knowledge"
        platform = "feishu" if binding.account_id.startswith("feishu:") else "weixin"
        scope = platform + ":" + digest(canonical(asdict(binding)).encode())
        plan = canonical(
            {
                "batch_id": batch_id,
                "binding_sha256": scope,
                "action": action,
                "question": question,
                "retention": retention,
                "sources": [entry[0] for entry in sources],
                **({"query_after_save": True} if query_after_save else {}),
                **({"target_vault_id": target_vault_id} if target_vault_id else {}),
                **(
                    {"query_existing": True, "query_vault_id": binding.vault_id}
                    if query_existing
                    else {}
                ),
                **({"retrieval_query": retrieval_query(question)} if intent and question else {}),
            }
        )
        plan_sha = digest(plan.encode())
        with database.transaction(journal):
            journal.execute(
                "CREATE TABLE IF NOT EXISTS wx_store_handoffs("
                "batch_id TEXT PRIMARY KEY REFERENCES wx_batches(id),account_id TEXT NOT NULL,"
                "plan_json TEXT NOT NULL,plan_sha256 TEXT NOT NULL,"
                "state TEXT NOT NULL CHECK(state IN ('prepared','stored')),"
                "results_json TEXT NOT NULL,results_sha256 TEXT NOT NULL) STRICT"
            )
            saved = journal.execute(
                "SELECT * FROM wx_store_handoffs WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if saved is not None:
                if (
                    saved["account_id"] != binding.account_id
                    or saved["plan_json"] != plan
                    or saved["plan_sha256"] != plan_sha
                ):
                    raise IntegrityError("handoff plan changed")
                if digest(saved["results_json"].encode()) != saved["results_sha256"]:
                    raise IntegrityError("handoff progress changed")
                journal.execute(
                    "UPDATE wx_store_handoffs SET state='prepared' WHERE batch_id=?", (batch_id,)
                )
            else:
                journal.execute(
                    "INSERT INTO wx_store_handoffs VALUES(?,?,?,?,'prepared','[]',?)",
                    (batch_id, binding.account_id, plan, plan_sha, digest(b"[]")),
                )
        main = database.connect(config.state_root)
        try:
            vault = auth.grant(
                main,
                binding.client_id,
                target_vault_id or binding.vault_id,
                "query" if query_existing else "add",
            )
            results = []
            for index, group in enumerate(groups):
                receipt = Store(config).ingest_locked(
                    main,
                    ingress_scope=scope,
                    event_id=f"{batch_id}:{index}",
                    vault=vault["name"],
                    source_key=f"{scope}:{batch_id}:{index}",
                    raw_event=canonical(
                        {
                            "batch_id": batch_id,
                            "action": action,
                            "sources": [entry[0] for entry in group],
                        }
                    ).encode(),
                    items=tuple(asset for entry in group for asset in entry[1]),
                    session_scope=scope,
                    session_id=batch_id,
                    retention=retention,
                    owner_client_id=binding.client_id,
                )
                if receipt.state != "stored":
                    raise IntegrityError("Store handoff did not complete")
                results.append(asdict(receipt))
                encoded = canonical(results)
                with database.transaction(journal):
                    journal.execute(
                        "UPDATE wx_store_handoffs SET results_json=?,results_sha256=? "
                        "WHERE batch_id=?",
                        (encoded, digest(encoded.encode()), batch_id),
                    )
            with database.transaction(journal):
                journal.execute(
                    "UPDATE wx_store_handoffs SET state='stored' WHERE batch_id=?", (batch_id,)
                )
        finally:
            main.close()
        return {
            "batch_id": batch_id,
            "store_state": "stored",
            "action": action,
            "question": question,
            "retention": retention,
            "receipts": results,
            "published": False,
            "dispatched": False,
            **({"query_after_save": True} if query_after_save else {}),
            **({"query_existing": True} if query_existing else {}),
            **({"retrieval_query": retrieval_query(question)} if intent and question else {}),
        }


def get(config: Config, binding: Binding, batch_id: str) -> dict[str, Any]:
    """Read local progress, including cancelled partial work; never resume as a side effect."""
    with inbox.connection(config, binding) as db:
        return read_locked(db, binding, batch_id)


def read_locked(db: sqlite3.Connection, binding: Binding, batch_id: str) -> dict[str, Any]:
    """Caller holds the authorized inbox connection and common storage lock."""
    inbox.bound(db, binding)
    batch = batches.snapshot(db, binding, batch_id)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='wx_store_handoffs'").fetchone():
        raise InvalidInput("no Store handoff")
    row = db.execute(
        "SELECT * FROM wx_store_handoffs WHERE batch_id=? AND account_id=?",
        (batch_id, binding.account_id),
    ).fetchone()
    if row is None:
        raise InvalidInput("no Store handoff")
    if (
        digest(row["plan_json"].encode()) != row["plan_sha256"]
        or digest(row["results_json"].encode()) != row["results_sha256"]
    ):
        raise IntegrityError("handoff ledger digest mismatch")
    plan = json.loads(row["plan_json"])
    intent = intents.read_locked(db, binding, batch_id)
    question = db.execute("SELECT question FROM wx_batches WHERE id=?", (batch_id,)).fetchone()[0]
    if bool(plan.get("query_after_save")) != intents.query_after_save(
        intent, plan["action"], question
    ) or (intent is not None and plan["question"] != question):
        raise IntegrityError("handoff consultation scope changed")
    if bool(plan.get("query_existing")) != weixin_note_queries.existing(intent) or (
        plan.get("query_existing")
        and (plan.get("query_vault_id") != binding.vault_id or plan["action"] != "consult")
    ):
        raise IntegrityError("handoff note query scope changed")
    return {
        "batch_id": batch_id,
        "batch_state": batch["state"],
        "store_state": row["state"],
        "action": plan["action"],
        "question": plan["question"],
        "retention": plan["retention"],
        "receipts": json.loads(row["results_json"]),
        "published": False,
        "dispatched": False,
        **({"query_after_save": True} if plan.get("query_after_save") else {}),
        **({"query_existing": True} if plan.get("query_existing") else {}),
        **({"retrieval_query": plan["retrieval_query"]} if "retrieval_query" in plan else {}),
    }
