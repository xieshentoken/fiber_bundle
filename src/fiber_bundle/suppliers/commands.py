"""Explicit supplier review/correction commands; no silent retries or file mutation."""

import time
from typing import Any

from .. import auth, objects
from ..config import Config, InvalidInput
from ..inputs import AssetInput, canonical
from ..store import Store
from . import ingestion, service, store
from .contracts import Correction, Offer


def version(value: dict[str, Any]) -> str:
    return objects.digest(canonical(value).encode())


def job_action(
    config: Config, db: Any, client: str, vault: str, identity: str, expected: str, action: str
) -> dict[str, Any]:
    service.authorize(config, db, client, vault, write=True)
    job = store.get(config, vault, "job", identity)
    if not job or job["client_id"] != client:
        raise auth.Denied(404)
    if version(job) != expected:
        raise InvalidInput("supplier_record_changed")
    if action == "cancel":
        if job["state"] == "complete":
            raise InvalidInput("supplier_already_applied")
        job["state"] = "cancelled"
    elif action == "approve":
        if job["state"] != "needs_review" or not job.get("offers"):
            raise InvalidInput("supplier_candidate_not_ready")
        ingestion.current_source(config, db, client, vault, job["source"])
        job["result"] = ingestion.apply_offers(
            config,
            db,
            client,
            vault,
            job["source"],
            [Offer.model_validate(o) for o in job["offers"]],
            supplier_id=job["supplier_id"],
            actor="user_review",
        )
        job["state"] = "complete"
    elif action == "resume":
        if job["state"] != "failed":
            raise InvalidInput("supplier_job_not_resumable")
        if any(
            c["job_id"] == identity and c["state"] != "succeeded"
            for c in store.rows(config, vault, "model_call")
        ):
            raise InvalidInput("supplier_model_result_unknown")
        ingestion.current_source(config, db, client, vault, job["source"])
        job.update(state="queued", error=None)
    else:
        raise InvalidInput("supplier_action_invalid")
    with store.connection(config, write=True) as side:
        assert side is not None
        store.put(side, vault, "job", job)
    return {**job, "version": version(job)}


FIELDS = {
    "supplier": {"name"},
    "product": {"name", "model"},
    "quote": {
        "amount",
        "currency",
        "unit",
        "quoted_at",
        "valid_until",
        "tax",
        "freight",
        "minimum_quantity",
        "maximum_quantity",
        "quantity_unit",
        "lead_time",
        "payment",
        "product_id",
    },
}


def correct(config: Config, db: Any, data: Correction) -> dict[str, Any]:
    service.authorize(config, db, data.client_id, data.vault_id, write=True)
    auth.grant(db, data.client_id, data.vault_id, "original")
    record = store.get(config, data.vault_id, data.kind, data.identity)
    if not record:
        raise auth.Denied(404)
    event = "correction-" + data.request_id
    prior = store.get(config, data.vault_id, "correction", event)
    request = canonical(data.model_dump())
    if prior:
        if prior["request_sha256"] != objects.digest(request.encode()):
            raise InvalidInput("supplier_request_conflict")
        return prior
    if version(record) != data.expected_sha256:
        raise InvalidInput("supplier_record_changed")
    if not set(data.fields) <= FIELDS[data.kind]:
        raise InvalidInput("supplier_correction_field_invalid")
    values = {k: (v or None) for k, v in data.fields.items()}
    if data.kind == "quote":
        if "product_id" in values:
            product = store.get(config, data.vault_id, "product", str(values["product_id"]))
            if (
                not product
                or not product.get("active", True)
                or product.get("supplier_id") != record.get("supplier_id")
            ):
                raise InvalidInput("supplier_product_binding_invalid")
        probe = {
            **{k: v for k, v in record.items() if k in Offer.model_fields},
            **record.get("overrides", {}),
            **values,
            "supplier_name": "manual",
            "product_name": "manual",
            "evidence_quote": data.reason,
        }
        probe.pop("product_id", None)
        Offer.model_validate(probe)
    elif any(not isinstance(v, str) or not v.strip() or len(v) > 1000 for v in values.values()):
        raise InvalidInput("supplier_correction_value_invalid")
    receipt = Store(config).ingest_locked(
        db,
        ingress_scope="supplier-correction:" + data.client_id,
        event_id=data.request_id,
        vault=auth.grant(db, data.client_id, data.vault_id, "add")["name"],
        source_key=event,
        raw_event=request.encode(),
        items=(
            AssetInput(request.encode(), "supplier-correction.json", "application/json", "body"),
        ),
        session_scope="supplier-correction",
        session_id=data.request_id,
        retention="knowledge",
        owner_client_id=data.client_id,
    )
    if receipt.state != "stored":
        raise InvalidInput("supplier_original_not_retained")
    correction = {
        "id": event,
        "kind": data.kind,
        "identity": data.identity,
        "fields": values,
        "reason": data.reason,
        "request_sha256": objects.digest(request.encode()),
        "receipt_id": receipt.job_id,
        "version_id": receipt.source_version_id,
        "actor": "user_correction",
        "created_at": time.time(),
    }
    record["overrides"] = {**record.get("overrides", {}), **values}
    record["user_revision"] = record.get("user_revision", 0) + 1
    record["correction_ids"] = [*record.get("correction_ids", []), event]
    with store.connection(config, write=True) as side:
        assert side is not None
        store.put(side, data.vault_id, "correction", correction)
        store.put(side, data.vault_id, data.kind, record)
    return correction
