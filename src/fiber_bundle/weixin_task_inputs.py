"""Adapt saved batches to existing task input contracts; no task submission or execution."""

import sqlite3
from typing import Any, Literal
from urllib.parse import urlsplit

from . import answer_tasks, article_bindings, auth, database, objects, weixin_note_queries
from . import weixin_inbox as inbox
from . import weixin_store as handoff
from .article_worker import CapturePolicy
from .articles import ArticleError, public_url
from .config import Config, IntegrityError, InvalidInput
from .inputs import AssetInput, canonical
from .store import Store
from .weixin_events import Binding
from .weixin_intent_text import retrieval_query


class InputPending(InvalidInput):
    """Only unsupported/ambiguous source shape, never authorization or storage failure."""


def _article(assets: list[AssetInput]) -> tuple[int, str]:
    bodies = [i for i, asset in enumerate(assets) if asset.role == "body"]
    if len(bodies) != 1:
        raise InputPending("article input requires one literal URL body")
    index = bodies[0]
    value = assets[index].data
    if value is None or len(value) > 4096:
        raise InputPending("invalid article URL size")
    try:
        literal = value.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise InputPending("invalid article URL encoding") from exc
    if any(character.isspace() for character in literal):
        raise InputPending("article body must be a literal URL")
    try:
        url = public_url(literal)
    except ArticleError as exc:
        raise InputPending("invalid article URL") from exc
    if urlsplit(url).hostname not in {
        "mp.weixin.qq.com",
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
    }:
        raise InputPending("unsupported article platform")
    return index, url


def _parent(
    config: Config,
    db: sqlite3.Connection,
    binding: Binding,
    batch_id: str,
    parent: dict[str, Any],
    vault: sqlite3.Row,
) -> tuple[sqlite3.Row, dict[str, Any], list[AssetInput]]:
    row = db.execute(
        "SELECT owner_client_id,source_version_id FROM receipts WHERE job_id=?",
        (parent["job_id"],),
    ).fetchone()
    if (
        row is None
        or row["owner_client_id"] != binding.client_id
        or row["source_version_id"] != parent["source_version_id"]
    ):
        raise auth.Denied(404)
    manifest = Store(config).verify_receipt_locked(db, parent["job_id"])
    if manifest["vault"] != vault["name"] or manifest["session_id"] != batch_id:
        raise IntegrityError("parent receipt scope changed")
    assets = []
    for item in manifest["assets"][1:]:
        data = objects.read_object(
            config.state_root,
            objects.object_relative(binding.vault_id, item["sha256"]),
            item["sha256"],
            item["byte_count"],
        )
        assets.append(AssetInput(data, item["original_name"], item["media_type"], item["role"]))
    return row, manifest, assets


def targets_locked(
    config: Config,
    db: sqlite3.Connection,
    binding: Binding,
    view: dict[str, Any],
) -> list[dict[str, Any]]:
    """Read-only task shape inspection. Caller holds the authorized journal/storage lock."""
    if view.get("query_existing"):
        auth.grant(db, binding.client_id, binding.vault_id, "query")
        return [{"batch_id": view["batch_id"], "kind": "answer", "parent_receipt_id": "notes"}]
    kind = "answer" if view["action"] == "consult" or view.get("query_after_save") else "article"
    vault = auth.grant(db, binding.client_id, binding.vault_id, "add")
    if kind == "answer":
        auth.grant(db, binding.client_id, binding.vault_id, "query")
        if (not view.get("query_after_save") and len(view["receipts"]) != 1) or not view[
            "question"
        ]:
            raise IntegrityError("consultation handoff scope changed")
    targets = []
    for parent in view["receipts"]:
        _, _, assets = _parent(config, db, binding, view["batch_id"], parent, vault)
        target = {"batch_id": view["batch_id"], "parent_receipt_id": parent["job_id"], "kind": kind}
        if kind == "article":
            try:
                _article(assets)
            except InputPending:
                target["reason"] = "article_input_requires_clarification"
        targets.append(target)
    if not targets:
        raise IntegrityError("empty handoff scope")
    if view.get("query_after_save"):
        return [{"batch_id": view["batch_id"], "parent_receipt_id": "batch", "kind": "answer"}]
    return targets


def _prepare(
    config: Config,
    binding: Binding,
    batch_id: str,
    kind: Literal["article", "answer"],
    parent_receipt_id: str | None,
) -> dict[str, Any]:
    with inbox.connection(config, binding) as journal:
        view = handoff.read_locked(journal, binding, batch_id)
        if view["batch_state"] != "selected" or view["store_state"] != "stored":
            raise InvalidInput("task input requires an active stored batch")
        if (kind == "answer") != (
            view["action"] == "consult" or bool(view.get("query_after_save"))
        ):
            raise InvalidInput("task kind conflicts with confirmed batch action")
        if view.get("query_existing"):
            if kind != "answer" or parent_receipt_id is not None:
                raise InvalidInput("bound-Vault query requires complete question scope")
            db = database.connect(config.state_root)
            try:
                auth.grant(db, binding.client_id, binding.vault_id, "query")
            finally:
                db.close()
            question = weixin_note_queries.question(view["question"])
            return {
                "input_kind": "notes",
                "receipt_id": weixin_note_queries.identity(batch_id),
                "question": question,
                "retrieval_query": retrieval_query(question),
                "submission_id": weixin_note_queries.identity(batch_id),
            }
        parents = view["receipts"]
        combined = kind == "answer" and bool(view.get("query_after_save"))
        if combined:
            parent_receipt_id = "batch"
        elif parent_receipt_id is None:
            if len(parents) != 1:
                raise InvalidInput("choose an exact parent receipt")
            parent_receipt_id = parents[0]["job_id"]
        matches = parents if combined else [r for r in parents if r["job_id"] == parent_receipt_id]
        if not matches or (not combined and len(matches) != 1):
            raise InvalidInput("receipt is outside the frozen handoff")
        db = database.connect(config.state_root)
        try:
            vault = auth.grant(db, binding.client_id, binding.vault_id, "add")
            if kind == "answer":
                auth.grant(db, binding.client_id, binding.vault_id, "query")
            assets = []
            provenance = []
            for parent in matches:
                row, manifest, material = _parent(config, db, binding, batch_id, parent, vault)
                assets.extend(material)
                provenance.append(
                    {
                        "parent_receipt_id": parent["job_id"],
                        "parent_source_version_id": row["source_version_id"],
                        "parent_assets": manifest["assets"][1:],
                    }
                )
            if kind == "article":
                index, url = _article(assets)
                assets[index] = AssetInput(url.encode(), "article-url.txt", "text/uri-list", "body")
            scope = "client:" + binding.client_id
            identity = f"weixin-task:{batch_id}:{parent_receipt_id}:{kind}"
            session = "weixin-task:" + batch_id
            receipt = Store(config).ingest_locked(
                db,
                ingress_scope=scope,
                event_id=identity,
                vault=vault["name"],
                source_key=scope + ":" + identity,
                raw_event=canonical(
                    {
                        "kind": kind,
                        "batch_id": batch_id,
                        "parent_receipt_id": parent_receipt_id,
                        **({"parents": provenance} if combined else provenance[0]),
                    }
                ).encode(),
                items=tuple(assets),
                session_scope=scope,
                session_id=session,
                retention="session",
                owner_client_id=binding.client_id,
            )
            if receipt.state != "stored":
                raise IntegrityError("task input not stored")
            # Reuse original task authorization rather than broadening its accepted scope.
            if kind == "article":
                article_bindings.receipt_source(config, db, binding.client_id, receipt.job_id)
            else:
                answer_tasks.source(config, db, binding.client_id, receipt.job_id, session)
            return {
                "receipt_id": receipt.job_id,
                "session_id": session,
                "question": view["question"],
                "retrieval_query": view.get("retrieval_query", view["question"]),
                "submission_id": identity,
            }
        finally:
            db.close()


def prepare_article(
    config: Config,
    binding: Binding,
    batch_id: str,
    policy: CapturePolicy,
    *,
    parent_receipt_id: str | None = None,
) -> article_bindings.ArticleInput:
    policy.validate()
    prepared = _prepare(config, binding, batch_id, "article", parent_receipt_id)
    return article_bindings.ArticleInput(prepared["receipt_id"], policy)


def prepare_answer(
    config: Config, binding: Binding, batch_id: str
) -> answer_tasks.AnswerInput | answer_tasks.NoteAnswerInput:
    prepared = _prepare(config, binding, batch_id, "answer", None)
    if prepared.get("input_kind") == "notes":
        return answer_tasks.NoteAnswerInput(
            vault_id=binding.vault_id,
            question=prepared["question"],
            retrieval_query=prepared["retrieval_query"],
            submission_id=prepared["submission_id"],
        )
    return answer_tasks.AnswerInput(
        receipt_id=prepared["receipt_id"],
        session_id=prepared["session_id"],
        question=prepared["question"],
        retrieval_query=prepared["retrieval_query"],
        submission_id=prepared["submission_id"],
    )
