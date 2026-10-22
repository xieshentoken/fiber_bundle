"""Exact authorized supplier queries and cautious comparisons; never model-written SQL."""

import re
from collections import Counter
from decimal import Decimal
from typing import Any

from .. import auth
from ..config import Config, InvalidInput
from . import ingestion, normalize, service, store
from .commands import version
from .contracts import Compare, Condition


def effective(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, **row.get("overrides", {}), "version": version(row)}


def dataset(config: Config, db: Any, client: str, vault: str) -> dict[str, Any]:
    service.authorize(config, db, client, vault)
    service.validate_sources(config, db, client, vault)
    sources = {s["id"]: s for s in store.rows(config, vault, "source") if s.get("active", True)}
    for source in sources.values():
        if not source.get("legacy"):
            ingestion.current_source(config, db, source["owner_client_id"], vault, source)
    result: dict[str, Any] = {"source": sources}
    for kind in ("supplier", "product", "quote", "contact"):
        result[kind] = {
            r["id"]: effective(r)
            for r in store.rows(config, vault, kind)
            if r.get("active", True)
            and (
                not r.get("source_id")
                or r.get("source_id") in sources
                and r.get("source_sha256") == sources[r["source_id"]]["sha256"]
            )
        }
    result["snapshot"] = version({k: result[k] for k in ("source", "supplier", "product", "quote")})
    return result


def evidence(source: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    quote = row.get("evidence_quote", row.get("price_raw", ""))
    text = source.get("text", "")
    start = text.find(quote) if quote else -1
    return {
        "source_id": source["id"],
        "sha256": source["sha256"],
        "original_state": source["original_state"],
        "knowledge_verified": False,
        "legacy": source.get("legacy", False),
        "path": source.get("path"),
        "start_char": start if start >= 0 else None,
        "end_char": start + len(quote) if start >= 0 else None,
        "quote": quote,
        "manual_correction_ids": row.get("correction_ids", []),
        "value_basis": "user_correction" if row.get("overrides") else "source_claim",
    }


def metric(raw: Any, target: str) -> Decimal | None:
    """Only exact dimensional conversions; mesh standards and ranges are not invented."""
    units = {
        "nm": Decimal("0.001"),
        "um": Decimal(1),
        "µm": Decimal(1),
        "μm": Decimal(1),
        "mm": Decimal(1000),
        "m": Decimal(1000000),
    }
    values = raw if isinstance(raw, list) else [raw]
    if len(values) != 1 or not isinstance(values[0], str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([^\s]*)\s*", values[0])
    if not match:
        return None
    amount, unit = match.groups()
    if unit == target:
        return Decimal(amount)
    if unit in units and target in units:
        return Decimal(amount) * units[unit] / units[target]
    return None


def spec_matches(product: dict[str, Any], data: Condition) -> bool:
    for spec in data.specs:
        raw = product.get("specs", {}).get(spec.field)
        values = raw if isinstance(raw, list) else [raw]
        if spec.equals is not None and spec.equals not in values:
            return False
        if spec.minimum is not None or spec.maximum is not None:
            amount = metric(raw, spec.unit)
            if (
                amount is None
                or (spec.minimum is not None and amount < Decimal(spec.minimum))
                or (spec.maximum is not None and amount > Decimal(spec.maximum))
            ):
                return False
    return True


def public(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        k: v
        for k, v in row.items()
        if k not in {"facts", "user_corrections", "overrides", "observation", "text"}
    }


def select(config: Config, db: Any, client: str, data: Condition) -> dict[str, Any]:
    rows = dataset(config, db, client, data.vault_id)
    if data.expected_snapshot and rows["snapshot"] != data.expected_snapshot:
        raise InvalidInput("supplier_query_snapshot_changed")
    items = []
    excluded: Counter[str] = Counter()
    iterable = rows[
        {"offers": "quote", "products": "product", "suppliers": "supplier"}[data.kind]
    ].values()
    for row in iterable:
        if data.kind == "offers":
            product = rows["product"].get(row.get("product_id"))
            supplier = rows["supplier"].get(row.get("supplier_id"))
        elif data.kind == "products":
            product, supplier = row, rows["supplier"].get(row.get("supplier_id"))
        else:
            product, supplier = None, row
        sid = supplier["id"] if supplier else None
        if data.supplier_id and data.supplier_id != sid:
            continue
        if not data.include_legacy and row.get("legacy"):
            excluded["legacy"] += 1
            continue
        if (
            data.kind != "suppliers"
            and not data.include_unresolved
            and (not product or not supplier)
        ):
            excluded["unresolved_identity"] += 1
            continue
        if data.text:
            contacts = [c for c in rows["contact"].values() if c.get("supplier_id") == sid]
            haystack = str([supplier, product, contacts]).casefold()
            if not all(t in haystack for t in data.text.casefold().split()):
                continue
        if data.model and (
            not product or (product.get("model") or "").casefold() != data.model.casefold()
        ):
            continue
        if data.category and data.category not in (product or supplier or {}).get("categories", []):
            continue
        if data.specs and (not product or not spec_matches(product, data)):
            excluded["spec_unknown_or_outside_condition"] += 1
            continue
        commercial = (
            data.currency
            or data.price_unit
            or data.price_min is not None
            or data.price_max is not None
            or data.tax
            or data.quoted_after
            or data.quoted_before
            or data.valid_on
        )
        if commercial and data.kind != "offers":
            raise InvalidInput("supplier_quote_filter_requires_offers")
        if data.currency and row.get("currency") != data.currency:
            excluded["currency_unknown_or_other"] += 1
            continue
        normalized = None
        if data.price_unit:
            normalized = (
                normalize.convert(row["amount"], row["unit"], data.price_unit)
                if row.get("amount") is not None and row.get("unit")
                else None
            )
            if normalized is None:
                excluded["price_or_unit_unknown_or_incompatible"] += 1
                continue
        if normalized is not None and (
            (data.price_min is not None and normalized < Decimal(data.price_min))
            or (data.price_max is not None and normalized > Decimal(data.price_max))
        ):
            continue
        if data.tax and row.get("tax") != data.tax:
            excluded["tax_unknown_or_other"] += 1
            continue
        quoted = row.get("quoted_at")
        if data.quoted_after or data.quoted_before:
            if not quoted:
                excluded["date_unknown"] += 1
                continue
            if (
                data.quoted_after
                and quoted < data.quoted_after
                or data.quoted_before
                and quoted > data.quoted_before
            ):
                continue
        if data.valid_on:
            until = row.get("valid_until")
            if not quoted or not until:
                excluded["validity_unknown"] += 1
                continue
            if not quoted <= data.valid_on <= until:
                continue
        source = rows["source"].get(row.get("source_id"))
        item = {
            **(public(row) or {}),
            "supplier": public(supplier),
            "product": public(product),
            "normalized_price": format(normalized, "f") if normalized is not None else None,
            "normalized_unit": data.price_unit,
            "evidence": evidence(source, row) if source else None,
        }
        items.append(item)
    items.sort(key=lambda r: r["id"])
    page = items[data.offset : data.offset + data.limit]
    return {
        "schema": "supplier-query/v1",
        "vault_id": data.vault_id,
        "snapshot": rows["snapshot"],
        "kind": data.kind,
        "total": len(items),
        "items": page,
        "next_offset": data.offset + data.limit if data.offset + data.limit < len(items) else None,
        "excluded": dict(excluded),
        "coverage": {
            "scope": "indexed supplier sources only",
            "source_count": len(rows["source"]),
            "originals_missing": sum(
                s["original_state"] == "missing" for s in rows["source"].values()
            ),
            "whole_vault_complete": False,
            "field_extraction_complete": False,
        },
        "groups": {
            "suppliers": dict(
                Counter((i.get("supplier") or {}).get("name", "unresolved") for i in items)
            )
        },
        "model_called": False,
        "vault_files_written": False,
    }


def compare(config: Config, db: Any, client: str, data: Compare) -> dict[str, Any]:

    rows = dataset(config, db, client, data.vault_id)
    ids = list(dict.fromkeys(data.quote_ids))
    if len(ids) < 2:
        raise InvalidInput("supplier_compare_requires_two")
    groups: dict[str, list[dict[str, Any]]] = {}
    unresolved = []
    for identity in ids:
        row = rows["quote"].get(identity)
        if row is None:
            raise auth.Denied(404)
        product = rows["product"].get(row.get("product_id"))
        if (
            not product
            or row.get("amount") is None
            or not row.get("currency")
            or not row.get("unit")
        ):
            unresolved.append(identity)
            continue
        target = "kg" if row["unit"] in normalize.MASS else row["unit"]
        amount = normalize.convert(row["amount"], row["unit"], target)
        # Unknown commercial terms never establish comparability.
        qualified = all(
            row.get(k) not in {None, "", "unknown"}
            for k in ("quoted_at", "valid_until", "tax", "freight")
        )
        basis = {
            "name": product["name"],
            "model": product.get("model"),
            "specs": product.get("specs", {}),
            "supplier_scope_when_specs_unknown": row.get("supplier_id")
            if not product.get("specs")
            else None,
            "currency": row["currency"],
            "unit": target,
            **{
                k: row.get(k)
                for k in (
                    "tax",
                    "freight",
                    "quoted_at",
                    "valid_until",
                    "minimum_quantity",
                    "maximum_quantity",
                    "quantity_unit",
                    "lead_time",
                    "payment",
                )
            },
        }
        key = version(basis) if qualified else "unknown-" + identity
        groups.setdefault(key, []).append(
            {
                **(public(row) or {}),
                "supplier": public(rows["supplier"].get(row.get("supplier_id"))),
                "product": public(product),
                "basis": basis,
                "normalized_price": format(amount, "f"),
                "evidence": evidence(rows["source"][row["source_id"]], row),
            }
        )
    values = [
        {
            "basis_sha256": key,
            "comparable": not key.startswith("unknown-") and len(items) > 1,
            "items": sorted(items, key=lambda r: Decimal(r["normalized_price"])),
        }
        for key, items in groups.items()
    ]
    return {
        "schema": "supplier-comparison/v1",
        "groups": values,
        "unresolved_quote_ids": unresolved,
        "snapshot": rows["snapshot"],
        "ranking_scope": "within identical explicit commercial and product bases only",
        "model_called": False,
        "vault_files_written": False,
    }


def classifications(config: Config, db: Any, client: str, vault: str) -> dict[str, Any]:
    rows = dataset(config, db, client, vault)
    row = service.authorize(config, db, client, vault)
    from .. import note_inventory

    inventory = note_inventory.read(config, db, row, registered=False)
    from .legacy import frontmatter

    existing = [
        {"path": n.relative_path, "title": n.title, "sha256": n.sha256}
        for n in inventory.notes
        if frontmatter(n.text).get("type") == "moc"
        or any(
            part.casefold() in {"map", "03-map", "04-map"}
            for part in n.relative_path.split("/")[:-1]
        )
    ]
    proposals = [
        {
            "product_id": p["id"],
            "supplier_id": p.get("supplier_id"),
            "source_id": p.get("source_id"),
            "categories": list(
                dict.fromkeys(
                    p.get("categories", [])
                    + normalize.categories(p["name"] + " " + str(p.get("specs", {})))
                )
            ),
            "state": "proposal_only",
        }
        for p in rows["product"].values()
    ]
    return {
        "schema": "supplier-classification-preview/v1",
        "proposals": proposals,
        "existing_moc_notes": existing,
        "next_action": (
            "Review/register a MOC in Organize & connect; this endpoint never writes it."
        ),
        "moc_write_required_user_confirmation": True,
        "archive_executed": False,
        "vault_files_written": False,
        "model_called": False,
    }
