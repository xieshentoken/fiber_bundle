"""Source retention and grounded structured candidates. No Vault/MOC file writes."""

import json
import time
from typing import Any

from .. import auth, document_evidence, objects
from ..config import Config, InvalidInput
from ..inputs import AssetInput, canonical
from ..store import Store
from . import service, store
from .contracts import DocumentImport, Extraction, ManualQuote, Offer
from .normalize import categories, money, phone


def stable(kind: str, *parts: Any) -> str:
    return kind + "-" + objects.digest(canonical(parts).encode())[:24]


def current_source(
    config: Config, db: Any, client: str, vault: str, source: dict[str, Any]
) -> None:
    service.authorize(config, db, client, vault)
    auth.grant(db, client, vault, "original")
    if source.get("path"):
        from .. import vaults
        from ..config import contained

        row = auth.grant(db, client, vault, "query")
        vaults.require_scope(config, row, source["path"])
        root = vaults.checked_vault(config, row)
        if service.observation(contained(root, source["path"])) != source["observation"]:
            raise InvalidInput("supplier_sources_changed")
    if source.get("run_id"):
        metadata, candidate = document_evidence.load(config, db, client, source["run_id"])
        if (
            metadata["result_sha256"] != source["result_sha256"]
            or metadata["source_version_id"] != source["version_id"]
        ):
            raise InvalidInput("supplier_sources_changed")
    else:
        Store(config).verify_receipt_locked(db, source["receipt_id"])
        receipt = db.execute(
            "SELECT source_version_id,owner_client_id FROM receipts WHERE job_id=?",
            (source["receipt_id"],),
        ).fetchone()
        if receipt is None or tuple(receipt) != (source["version_id"], client):
            raise auth.Denied(404)


def validate_extraction(raw: str, text: str) -> Extraction:
    from ..document_capture import reject_duplicate

    value = raw.strip()
    if value.startswith("```") and value.endswith("```"):
        value = value.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        result = Extraction.model_validate(json.loads(value, object_pairs_hook=reject_duplicate))
    except (ValueError, TypeError) as error:
        raise InvalidInput("supplier_model_response_invalid") from error
    for offer in result.offers:
        quote = offer.evidence_quote
        if quote not in text:
            raise InvalidInput("supplier_evidence_quote_missing")
        # Bind each row to its own product/model, preventing a price borrowed from another row.
        if offer.product_name not in quote and (not offer.model or offer.model not in quote):
            raise InvalidInput("supplier_product_evidence_missing")
        company = offer.company_evidence or quote
        if company not in text or offer.supplier_name not in company:
            raise InvalidInput("supplier_company_evidence_missing")
        for item in [
            offer.model,
            offer.amount,
            offer.quoted_at,
            offer.valid_until,
            offer.minimum_quantity,
            offer.maximum_quantity,
            offer.lead_time,
            offer.payment,
            offer.contact_name,
            offer.phone,
            *offer.specs.values(),
        ]:
            if item and item not in quote:
                raise InvalidInput("supplier_field_evidence_missing")
        if offer.amount is not None and money(quote) != {
            "amount": offer.amount,
            "currency": offer.currency,
            "unit": offer.unit,
        }:
            raise InvalidInput("supplier_quote_evidence_mismatch")
        if offer.amount is not None and (offer.currency is None or offer.unit is None):
            raise InvalidInput("supplier_quote_basis_unknown")
        # Terms must be explicit in the cited row; an absence never becomes excluded/zero.
        for value, yes, no in [
            (offer.tax, ("含税", "tax included"), ("不含税", "未税", "tax excluded")),
            (
                offer.freight,
                ("含运", "包邮", "freight included"),
                ("不含运", "运费另", "freight excluded"),
            ),
        ]:
            if value == "included" and (
                not any(w in quote for w in yes) or any(w in quote for w in no)
            ):
                raise InvalidInput("supplier_terms_evidence_missing")
            if value == "excluded" and not any(w in quote for w in no):
                raise InvalidInput("supplier_terms_evidence_missing")
    return result


def apply_offers(
    config: Config,
    db: Any,
    client: str,
    vault: str,
    source: dict[str, Any],
    offers: list[Offer],
    *,
    supplier_id: str | None = None,
    actor: str = "model",
) -> dict[str, Any]:
    service.authorize(config, db, client, vault, write=True)
    current_source(config, db, client, vault, source)
    suppliers = store.rows(config, vault, "supplier")
    selected = next((s for s in suppliers if s["id"] == supplier_id), None)
    if supplier_id and not selected:
        raise InvalidInput("supplier_entity_missing")
    with store.connection(config, write=True) as side:
        assert side is not None
        store.put(side, vault, "source", source)
        made = []
        for offer in offers:
            matching = [s for s in suppliers if s["name"] == offer.supplier_name]
            if len(matching) > 1:
                raise InvalidInput("supplier_identity_ambiguous")
            supplier = selected or (matching[0] if matching else None)
            if selected and selected["name"] != offer.supplier_name:
                raise InvalidInput("supplier_identity_mismatch")
            if supplier is None:
                supplier = {
                    "id": stable("supplier", vault, offer.supplier_name),
                    "name": offer.supplier_name,
                    "categories": categories(offer.product_name),
                    "facts": [],
                    "legacy": False,
                    "knowledge_verified": False,
                    "user_corrections": "",
                }
                suppliers.append(supplier)
                store.put(side, vault, "supplier", supplier)
            product = {
                "id": stable(
                    "product",
                    supplier["id"],
                    source["id"],
                    offer.product_name,
                    offer.model,
                    offer.specs,
                ),
                "supplier_id": supplier["id"],
                "source_id": source["id"],
                "source_sha256": source["sha256"],
                "name": offer.product_name,
                "model": offer.model,
                "specs": offer.specs,
                "categories": list(
                    dict.fromkeys(
                        [offer.category] if offer.category else categories(offer.product_name)
                    )
                ),
                "legacy": False,
                "knowledge_verified": False,
                "active": True,
            }
            store.put(side, vault, "product", product)
            quote = {
                "id": stable("quote", source["id"], offer.model_dump()),
                "supplier_id": supplier["id"],
                "product_id": product["id"],
                "source_id": source["id"],
                "source_sha256": source["sha256"],
                **{
                    k: v
                    for k, v in offer.model_dump().items()
                    if k
                    not in {
                        "supplier_name",
                        "product_name",
                        "category",
                        "specs",
                        "model",
                        "phone",
                        "contact_name",
                    }
                },
                "legacy": False,
                "active": True,
                "knowledge_verified": False,
                "actor": actor,
                "state": "recorded",
                "created_at": time.time(),
            }
            store.put(side, vault, "quote", quote)
            made.append(quote["id"])
            if offer.phone or offer.contact_name:
                store.put(
                    side,
                    vault,
                    "contact",
                    {
                        "id": stable("contact", source["id"], offer.contact_name, offer.phone),
                        "supplier_id": supplier["id"],
                        "source_id": source["id"],
                        "source_sha256": source["sha256"],
                        "phone_raw": offer.phone,
                        "phone": phone(offer.phone),
                        "names": [offer.contact_name] if offer.contact_name else [],
                        "legacy": False,
                        "active": True,
                        "knowledge_verified": False,
                    },
                )
        return {
            "state": "recorded",
            "quote_ids": made,
            "original_state": "retained",
            "vault_files_written": False,
        }


def manual(config: Config, db: Any, data: ManualQuote) -> dict[str, Any]:
    service.authorize(config, db, data.client_id, data.vault_id, write=True)
    auth.grant(db, data.client_id, data.vault_id, "original")
    wire = canonical(data.model_dump())
    digest = objects.digest(wire.encode())
    with store.connection(config) as side:
        prior = (
            side.execute(
                "SELECT digest,result FROM operations WHERE vault=? AND id=?",
                (data.vault_id, data.request_id),
            ).fetchone()
            if side
            else None
        )
    if prior:
        if prior[0] != digest:
            raise InvalidInput("supplier_request_conflict")
        return dict(json.loads(prior[1]))
    receipt = Store(config).ingest_locked(
        db,
        ingress_scope="supplier-manual:" + data.client_id,
        event_id=data.request_id,
        vault=auth.grant(db, data.client_id, data.vault_id, "add")["name"],
        source_key="supplier-manual:" + data.request_id,
        raw_event=wire.encode(),
        items=(AssetInput(wire.encode(), "manual-quote.json", "application/json", "body"),),
        session_scope="supplier-manual",
        session_id=data.request_id,
        retention="knowledge",
        owner_client_id=data.client_id,
    )
    if receipt.state != "stored":
        raise InvalidInput("supplier_original_not_retained")
    source = {
        "id": stable("source", data.vault_id, data.request_id),
        "title": data.offer.supplier_name + " · " + data.offer.product_name,
        "sha256": digest,
        "text": wire,
        "legacy": False,
        "original_state": "retained",
        "knowledge_verified": False,
        "receipt_id": receipt.job_id,
        "version_id": receipt.source_version_id,
        "owner_client_id": data.client_id,
        "captured_at": time.time(),
        "source_kind": "user_entry",
    }
    result = apply_offers(
        config,
        db,
        data.client_id,
        data.vault_id,
        source,
        [data.offer],
        supplier_id=data.supplier_id,
        actor="user_entry",
    )
    result = {**result, "source_id": source["id"]}
    with store.connection(config, write=True) as side:
        assert side is not None
        side.execute(
            "INSERT INTO operations VALUES(?,?,?,?)",
            (data.vault_id, data.request_id, digest, canonical(result)),
        )
    return result


def document(config: Config, db: Any, data: DocumentImport) -> dict[str, Any]:
    service.authorize(config, db, data.client_id, data.vault_id, write=True)
    auth.grant(db, data.client_id, data.vault_id, "original")
    metadata, candidate = document_evidence.load(config, db, data.client_id, data.run_id)
    if metadata["vault_id"] != data.vault_id:
        raise auth.Denied(404)
    units = candidate["pages"] if candidate["schema"] == 1 else candidate["segments"]
    if candidate.get("complete") is False:
        raise InvalidInput("supplier_source_incomplete")
    published = db.execute(
        "SELECT 1 FROM document_publications WHERE run_id=? AND state='published'", (data.run_id,)
    ).fetchone()
    if not published and not metadata["review_gate"]["publication_ready"]:
        raise InvalidInput("supplier_source_review_required")
    text = "\n\n".join(u["text"] for u in units)
    if not text.strip():
        raise InvalidInput("supplier_source_text_empty")
    source = {
        "id": stable("source", data.vault_id, data.run_id),
        "title": db.execute(
            "SELECT original_name FROM version_assets WHERE version_id=? AND asset_id=?",
            (metadata["source_version_id"], metadata["asset_id"]),
        ).fetchone()[0],
        "sha256": metadata["source_sha256"],
        "text": text,
        "units": [
            {
                "text": u["text"],
                "locator": u.get("locator", {"physical_page": u.get("physical_page")}),
            }
            for u in units
        ],
        "legacy": False,
        "original_state": "retained",
        "knowledge_verified": False,
        "run_id": data.run_id,
        "version_id": metadata["source_version_id"],
        "asset_id": metadata["asset_id"],
        "result_sha256": metadata["result_sha256"],
        "owner_client_id": data.client_id,
        "captured_at": time.time(),
    }
    identity = "document-" + data.run_id
    prior = store.get(config, data.vault_id, "job", identity)
    if prior:
        return prior
    job: dict[str, Any] = {
        "id": identity,
        "vault_id": data.vault_id,
        "client_id": data.client_id,
        "supplier_id": data.supplier_id,
        "source": source,
        "state": "queued",
        "created_at": time.time(),
        "windows": [],
        "error": None,
    }
    with store.connection(config, write=True) as side:
        assert side is not None
        store.put(side, data.vault_id, "source", source)
        store.put(side, data.vault_id, "job", job)
    return job
