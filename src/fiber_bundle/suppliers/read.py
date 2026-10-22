"""Safe user-facing projections and original/evidence access."""

from typing import Any

from .. import auth, operations
from ..config import Config
from . import ingestion, service, store
from .commands import version


def summary(config: Config, db: Any, client: str, vault: str) -> dict[str, Any]:
    service.authorize(config, db, client, vault)
    service.validate_sources(config, db, client, vault)
    for source in store.rows(config, vault, "source"):
        if source.get("active", True) and not source.get("legacy"):
            ingestion.current_source(config, db, source["owner_client_id"], vault, source)
    result = {
        kind: [
            {**r, **r.get("overrides", {}), "version": version(r)}
            for r in store.rows(config, vault, kind)
        ]
        for kind in ["supplier", "alias", "contact", "product", "quote", "issue", "correction"]
    }
    result["source"] = [
        {k: v for k, v in r.items() if k not in {"text", "units", "facts"}}
        for r in store.rows(config, vault, "source")
    ]
    result["job"] = [
        {
            k: v
            for k, v in {
                **r,
                "version": version(r),
                "source_title": r["source"]["title"],
                "source_id": r["source"]["id"],
            }.items()
            if k != "source"
        }
        for r in store.rows(config, vault, "job")
    ]
    return {
        "schema": "supplier-workspace/v1",
        "records": result,
        "profile": store.profile(config, vault),
        "model_called": False,
        "vault_files_written": False,
    }


def evidence(config: Config, db: Any, client: str, vault: str, identity: str) -> dict[str, Any]:
    service.authorize(config, db, client, vault)
    source = store.get(config, vault, "source", identity)
    if not source:
        raise auth.Denied(404)
    if source.get("legacy"):
        service.validate_sources(config, db, client, vault)
    else:
        ingestion.current_source(config, db, source["owner_client_id"], vault, source)
    return {
        "source_id": identity,
        "title": source["title"],
        "text": source["text"],
        "units": source.get("units", []),
        "original_state": source["original_state"],
        "legacy": source["legacy"],
        "knowledge_verified": False,
        "path": source.get("path"),
        "sha256": source["sha256"],
        "assets": [
            {"id": r[0], "name": r[1]}
            for r in db.execute(
                "SELECT asset_id,original_name FROM version_assets WHERE "
                "version_id=? AND role!='event' ORDER BY ordinal",
                (source.get("version_id"),),
            )
        ]
        if source["original_state"] == "retained"
        else [],
    }


def original(
    config: Config, db: Any, client: str, vault: str, identity: str, asset_id: str | None = None
) -> tuple[bytes, str, str]:
    from ..config import InvalidInput

    source = store.get(config, vault, "source", identity)
    service.authorize(config, db, client, vault)
    auth.grant(db, client, vault, "original")
    if not source:
        raise auth.Denied(404)
    if source["original_state"] != "retained":
        raise InvalidInput("supplier_original_missing")
    # Revalidate the captured owner; delivery also requires the requester's original grant.
    ingestion.current_source(config, db, source["owner_client_id"], vault, source)
    assets = db.execute(
        "SELECT a.id,va.original_name,va.media_type FROM version_assets va JOIN "
        "assets a ON a.id=va.asset_id WHERE va.version_id=? AND a.vault_id=? AND "
        "va.role!='event' ORDER BY va.ordinal",
        (source["version_id"], vault),
    ).fetchall()
    asset = next(
        (a for a in assets if a[0] == (asset_id or source.get("asset_id"))),
        assets[0] if assets else None,
    )
    if asset is None or (asset_id is not None and asset[0] != asset_id):
        raise auth.Denied(404)
    return (
        operations.original(
            config, db, client, vault, source["version_id"], asset[0], 16 * 1024 * 1024
        ),
        asset[1],
        asset[2],
    )


def markdown(config: Config, db: Any, client: str, vault: str, identity: str) -> str:
    service.authorize(config, db, client, vault)
    supplier = store.get(config, vault, "supplier", identity)
    if not supplier:
        raise auth.Denied(404)
    lines = [
        "# " + supplier.get("overrides", {}).get("name", supplier["name"]),
        "",
        "供应商事实投影 / Supplier facts projection",
        "",
        "所有记录保留来源；历史原件缺失与未核验状态见每条记录。",
        "",
    ]
    products = {
        p["id"]: {**p, **p.get("overrides", {})} for p in store.rows(config, vault, "product")
    }
    for q in store.rows(config, vault, "quote"):
        if q["supplier_id"] != identity or not q.get("active", True):
            continue
        q = {**q, **q.get("overrides", {})}
        p = products.get(q["product_id"], {})
        lines.extend(
            [
                "## " + str(p.get("name", "待确认产品")),
                "",
                f"- 型号：{p.get('model') or '未知'}",
                f"- 报价：{q.get('amount') or '未知'} {q.get('currency') or ''}"
                f"/{q.get('unit') or '未知'}",
                f"- 日期：{q.get('quoted_at') or '未知'}",
                f"- 来源：{q['source_id']}；"
                f"{'历史原件缺失' if q.get('legacy') else '原件保留'}；事实未独立核验",
                "",
            ]
        )
    lines.extend(["## 用户补充与修正", "", supplier.get("user_corrections", "")])
    return "\n".join(lines)


def bounded_evidence(config: Config, db: Any, client: str, data: Any) -> dict[str, Any]:
    from ..config import InvalidInput

    result = evidence(config, db, client, data.vault_id, data.source_id)
    if result["sha256"] != data.expected_sha256:
        raise InvalidInput("supplier_sources_changed")
    text = result.pop("text")
    result.pop("units", None)
    result.update(
        text=text[data.offset : data.offset + data.length],
        start_char=data.offset,
        next_offset=data.offset + data.length if data.offset + data.length < len(text) else None,
        total_characters=len(text),
    )
    return result
