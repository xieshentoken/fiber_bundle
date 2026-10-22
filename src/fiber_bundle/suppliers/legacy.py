"""Read-only Gsup Markdown adapter; never fabricates missing original evidence."""

import json
import re
from collections import defaultdict
from typing import Any

from ..inputs import canonical
from ..objects import digest
from . import normalize

START, END = "<!-- GF_AGENT_MANAGED_START -->", "<!-- GF_AGENT_MANAGED_END -->"


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    result = {}
    if match:
        for line in match[1].splitlines():
            field = re.match(r"^([a-z_][\w-]*):\s*(.*?)\s*$", line)
            if field:
                key, value = field.groups()
                if value.startswith('"'):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        value = value.strip('"')
                result[key] = str(value).strip("'")
    return result


def managed(text: str) -> str:
    if START not in text:
        return text
    return text.split(START, 1)[1].split(END, 1)[0]


def facts(text: str) -> list[dict[str, str]]:
    result = []
    for match in re.finditer(
        r"^- \*\*([a-z_]+)\*\*：(.+?)(?=^- \*\*|^###|\Z)", managed(text), re.S | re.M
    ):
        value = match[2].strip()
        evidence = re.findall(r"SRC-[A-Z0-9-]+(?:-E\d+)?", value)
        value = re.sub(r"（[^（）]*(?:SRC-[A-Z0-9-]+)[^（）]*）\s*$", "", value).strip()
        if value:
            result.append(
                {
                    "field": match[1],
                    "value": value,
                    "evidence_id": evidence[-1] if evidence else "",
                    "quote": match[0].strip(),
                }
            )
    return result


def safe_product(value: str) -> bool:
    return bool(
        value
        and len(value) < 200
        and not any(
            t in value for t in ["信息原文", "材料编号：", "单位结算", "待补充", "#供应信息保存"]
        )
    )


def project(notes: list[dict[str, Any]]) -> dict[str, Any]:
    records: dict[str, list[dict[str, Any]]] = {
        k: [] for k in ["supplier", "alias", "source", "contact", "product", "quote", "issue"]
    }
    by_id = {}
    source_owners: dict[str, set[str]] = defaultdict(set)
    for note in notes:
        meta = frontmatter(note["text"])
        kind = meta.get("type")
        identity = meta.get("supplier_id")
        if kind == "supplier" and identity:
            correction = (
                note["text"]
                .split("## 用户补充与修正区", 1)[-1]
                .split("## 待确认事项", 1)[0]
                .strip()
                if "## 用户补充与修正区" in note["text"]
                else ""
            )
            item = {
                "id": identity,
                "name": note["title"],
                "path": note["path"],
                "sha256": note["sha256"],
                "observation": note.get("observation"),
                "legacy": True,
                "knowledge_verified": False,
                "facts": facts(note["text"]),
                "user_corrections": correction,
                "categories": normalize.categories(managed(note["text"])),
            }
            by_id[identity] = item
            records["supplier"].append(item)
            for sid in re.findall(r"SRC-\d{8}-[A-F0-9]{10}", managed(note["text"])):
                source_owners[sid].add(identity)
        elif kind == "supplier_alias":
            old = re.match(r"SUP-[A-F0-9]+", note["path"].split("/")[-1])
            target = re.search(r"SUP-[A-F0-9]+", meta.get("canonical_supplier", ""))
            if old and target:
                records["alias"].append(
                    {"id": old[0], "canonical_id": target[0], "path": note["path"], "legacy": True}
                )
    for note in notes:
        meta = frontmatter(note["text"])
        if meta.get("type") != "supplier_source" or not meta.get("source_id"):
            continue
        sid = meta["source_id"]
        owners = source_owners.get(sid, set())
        supplier_id = next(iter(owners)) if len(owners) == 1 else None
        fs = facts(note["text"])
        data = defaultdict(list)
        for f in fs:
            data[f["field"]].append(f["value"])
        source = {
            "id": sid,
            "title": note["title"],
            "path": note["path"],
            "sha256": note["sha256"],
            "observation": note.get("observation"),
            "text": note["text"],
            "supplier_id": supplier_id,
            "facts": fs,
            "legacy": True,
            "original_state": "missing",
            "knowledge_verified": False,
            "captured_at": meta.get("created"),
            "legacy_confidentiality": meta.get("confidentiality"),
            "extraction_state": "historical_record",
            "historical_complete": meta.get("status") == "complete",
        }
        records["source"].append(source)
        reasons = []
        if not fs:
            reasons.append("no_structured_facts")
        if not supplier_id:
            reasons.append("supplier_unresolved")
        if any(not safe_product(v) for v in data["item_name"] + data["main_products"]):
            reasons.append("product_placeholder")
        if any(not normalize.valid_date(v) for v in data["test_date"]):
            reasons.append("date_not_normalized")
        for value in data["phone"]:
            records["contact"].append(
                {
                    "id": "contact-" + digest(canonical([sid, value]).encode())[:20],
                    "supplier_id": supplier_id,
                    "source_id": sid,
                    "phone_raw": value,
                    "phone": normalize.phone(value),
                    "names": data["contact_name"],
                    "legacy": True,
                    "knowledge_verified": False,
                }
            )
        models = list(dict.fromkeys(data["model"]))
        names = [v for v in data["item_name"] if safe_product(v)]
        # A source with several models cannot establish which quote belongs to which model.
        product = None
        if supplier_id and (len(models) == 1 or (not models and len(names) == 1)):
            product = {
                "id": "product-"
                + digest(canonical([supplier_id, sid, models, names]).encode())[:20],
                "supplier_id": supplier_id,
                "source_id": sid,
                "name": names[0] if names else models[0],
                "model": models[0] if models else None,
                "specs": {
                    k: list(dict.fromkeys(v))
                    for k, v in data.items()
                    if k
                    not in {
                        "price",
                        "phone",
                        "contact_name",
                        "website",
                        "address",
                        "bank_account",
                        "bank_name",
                        "taxpayer_id",
                    }
                },
                "categories": normalize.categories(managed(note["text"])),
                "legacy": True,
                "knowledge_verified": False,
            }
            records["product"].append(product)
        for index, price in enumerate(data["price"]):
            parsed = normalize.money(price)
            quote = {
                "id": "quote-" + digest(canonical([sid, index, price]).encode())[:20],
                "source_id": sid,
                "supplier_id": supplier_id,
                "product_id": product["id"] if product else None,
                "price_raw": price,
                **parsed,
                "quoted_at": None,
                "valid_until": None,
                "tax": "unknown",
                "freight": "unknown",
                "minimum_quantity": None,
                "legacy": True,
                "knowledge_verified": False,
                "state": "provisional" if product and parsed["amount"] else "needs_review",
            }
            records["quote"].append(quote)
            if not product:
                reasons.append("quote_product_unresolved")
            if parsed["amount"] is None:
                reasons.append("quote_basis_unknown")
        for reason in dict.fromkeys(reasons):
            records["issue"].append(
                {
                    "id": sid + ":" + reason,
                    "source_id": sid,
                    "reason": reason,
                    "state": "open",
                    "legacy": True,
                }
            )
    sources = {r["id"]: r for r in records["source"]}
    for kind in ["contact", "product", "quote", "issue"]:
        for item in records[kind]:
            item["source_sha256"] = sources[item["source_id"]]["sha256"]
            item["active"] = True
    for alias in records["alias"]:
        if alias["canonical_id"] not in by_id:
            raise ValueError("supplier_alias_target_missing")
    ids = [(kind, r["id"]) for kind, rows in records.items() for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("supplier_legacy_duplicate_id")
    return {
        "schema": "supplier-legacy/v1",
        "records": records,
        "counts": {k: len(v) for k, v in records.items()},
        "snapshot_sha256": digest(canonical(notes).encode()),
        "notes": notes,
        "vault_files_written": False,
        "model_called": False,
    }
