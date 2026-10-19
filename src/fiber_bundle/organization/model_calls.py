"""Durable classification model admission and shared-budget accounting."""

import time
from dataclasses import asdict, replace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .. import database, model_budgets, model_gateway
from ..config import InvalidInput
from ..inputs import canonical
from ..objects import digest
from . import packages, store
from .model_evidence import VERSION, EvidenceInvalid
from .model_unknown import UnknownCall, detail


def record_validation(
    config: Any, job: Any, key: str, raw: str, error: EvidenceInvalid | None = None
) -> None:
    """Attach evidence status to a received reply, preserving transport/usage history.

    Legacy replies have no validation marker and must be checked again. An invalid
    marker never authorizes another send; a newer validator can recheck the same bytes.
    """
    validation = {
        "version": VERSION,
        "state": "invalid" if error else "validated",
        "reason": error.reason if error else None,
        "reply_sha256": digest(raw.encode()),
    }
    with database.locked(config):
        current = store.get(config, "model_reviews", key)
        if not current or current["state"] != "succeeded" or current.get("reply") != raw:
            return
        if current.get("evidence_validation") != validation:
            current["evidence_validation"] = validation
            store.put(config, "model_reviews", key, job["vault_id"], current)


def execute(
    config: Any, job: Any, selected: Any, messages: Any, key: str, check: Any, resolve_policy: Any
) -> str:
    check()
    with database.locked(config), database.connect(config.state_root) as db:
        active = store.get(config, "jobs", job["id"])
        if (
            active
            and active.get("maintenance")
            and (
                active["state"] not in {"queued", "running"}
                or active.get("generation", 0) != job.get("generation", 0)
            )
        ):
            raise InvalidInput("maintenance_cancelled")
        prior = store.get(config, "model_reviews", key)
        if prior and prior["state"] == "succeeded":
            # Transport succeeded; resolve must still validate these original bytes.
            return str(prior["reply"])
        # Classification needs room for reasoning plus a final evidence object;
        # some reasoning providers consume 4096 tokens before producing text.
        # This is a request window, charged to the same host-owned budget.
        limit = max(selected.max_output_tokens, 8192)
        if prior:
            if prior["state"] not in {"invalid_response", "retry_authorized"}:
                info = detail(db, key, prior)
                if prior["state"] == "sending" and info["state"] == "failed":
                    prior.update(state="unknown", error_code=info["reason"])
                    store.put(config, "model_reviews", key, job["vault_id"], prior)
                raise UnknownCall(info)
            if prior["state"] == "retry_authorized":
                limit = prior["output_limit"]
            elif not prior.get("retryable") or prior["output_limit"] >= 32768:
                raise InvalidInput("organization_model_response_invalid")
            # Only a known empty/length-limited response can expand its output
            # window. Unknown transport results never enter this branch.
            else:
                limit = min(prior["output_limit"] * 2, 32768)
        effective = replace(selected, max_output_tokens=limit)
        payload = model_gateway.prepare(effective, {"messages": messages})
        if "client_id" in job:
            packages.context(config, db, job["client_id"], job["vault_id"], write=True)
            if resolve_policy(db).digest != selected.digest:
                raise InvalidInput("organization_model_policy_changed")
            epoch = db.execute(
                "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
                (job["client_id"], job["vault_id"]),
            ).fetchone()
            if "permission_epoch" in job and (epoch[0] if epoch else 0) != job["permission_epoch"]:
                raise InvalidInput("organization_authorization_changed")
        if len(canonical(messages)) + 512 > selected.max_input_tokens:
            raise InvalidInput("organization_model_input_window_required")
        attempt = prior.get("retry_number", 0) if prior else 0
        identity = str(
            uuid5(
                NAMESPACE_URL,
                "fiber-classification:"
                + key
                + ":output:"
                + str(limit)
                + (":retry:" + str(attempt) if attempt else ""),
            )
        )
        with database.transaction(db):
            # An admission-only crash is conservative, even before the cache row.
            if db.execute(
                "SELECT 1 FROM model_configuration_calls WHERE id=?", (identity,)
            ).fetchone():
                raise UnknownCall(detail(db, key, {"id": identity}))
            reservation = model_budgets.reserve(
                db, selected.secret_file, selected.max_input_tokens + limit, 0
            )
            configuration_id = getattr(selected, "configuration_id", None)
            if not configuration_id:
                raise InvalidInput("organization_registered_model_required")
            credential = db.execute(
                "SELECT credential_id FROM model_configurations WHERE id=?", (configuration_id,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO model_configuration_calls "
                "VALUES(?,?,?,?,?,'running',?,?,NULL,NULL,NULL,NULL,?)",
                (
                    identity,
                    configuration_id,
                    credential,
                    selected.provider,
                    selected.model,
                    canonical(
                        {**asdict(effective), "purpose": "organization", "job_id": job["id"]}
                    ),
                    reservation or "",
                    time.time(),
                ),
            )
        history = list(prior.get("attempts", [])) if prior else []
        if prior:
            history.append({k: v for k, v in prior.items() if k not in {"attempts", "reply"}})
        row = {
            "id": identity,
            "job": job["id"],
            "state": "sending",
            "reservation": reservation,
            "configuration_sha256": selected.digest,
            "output_limit": limit,
            "attempts": history,
            "retry_number": attempt,
            "created_at": time.time(),
        }
        store.put(config, "model_reviews", key, job["vault_id"], row)
        current_job = store.get(config, "jobs", job["id"])
        if current_job and current_job["state"] in {"queued", "running"}:
            current_job["model_called"] = True
            store.put(config, "jobs", job["id"], job["vault_id"], current_job)
    from .model_transport import exchange, wait

    valid, content = wait(config, job, lambda: exchange(config, job, effective, key, row, payload))
    check()
    if not valid:
        if row["retryable"] and limit < 32768:
            return execute(config, job, selected, messages, key, check, resolve_policy)
        raise InvalidInput("organization_model_response_invalid")
    return str(content)
