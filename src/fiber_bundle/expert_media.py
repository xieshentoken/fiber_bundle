"""Figure-ID based media reads with current permissions and per-resource failure isolation."""

import base64
import mimetypes
import sqlite3
from typing import Any

from . import auth, published_expert
from .config import Config, IntegrityError, InvalidInput
from .objects import digest


def references(result: dict[str, Any]) -> list[dict[str, Any]]:
    seen = set()
    media = []
    for figure in result.get("figures", []):
        identity = figure.get("id")
        if not isinstance(identity, str) or identity in seen:
            continue
        seen.add(identity)
        image = figure.get("image")
        image_sha = image.get("sha256") if isinstance(image, dict) else figure.get("image_sha256")
        media.append(
            {
                "resource_id": identity,
                "object_ids": figure.get("object_ids", []),
                "kind": "table" if figure.get("kind") == "table" else "image",
                "caption": str(figure.get("caption") or figure.get("label") or identity),
                "sha256": image_sha,
                "status": "unsupported",
                "reason": "output_capability_unknown",
            }
        )
    return media


def block(packet: dict[str, Any]) -> dict[str, Any]:
    result = packet["query_result"]
    return {
        "module_id": packet["module_id"],
        "version": packet["version"],
        "source_version_id": packet.get("source_version_id"),
        "artifact_stage": "draft",
        "review_state": "deferred",
        "knowledge_verified": False,
        "independent_evidence": False,
        "behavior": result.get("behavior", "abstain"),
        "reason_codes": result.get("reason_codes", []),
        "evidence_ids": result.get("evidence_ids", []),
        "object_ids": result.get("matched_object_ids", []),
        "media": references(result),
        "text": "\n\n".join(
            str(o.get("statement") or o.get("title") or "") for o in result.get("results", [])
        )
        or (
            "图表与问题相关，但没有足够文字证据支持事实结论。"
            if result.get("figures")
            else "专家参考包没有足够文字证据支持事实结论。"
        ),
    }


def read(
    config: Config,
    db: sqlite3.Connection,
    cid: str,
    module_id: str,
    expected: str,
    resource_id: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "resource_id": resource_id,
        "module_id": module_id,
        "version": expected,
    }
    try:
        if not module_id.startswith("expert:"):
            raise auth.Denied(404)
        module = published_expert.text(config, db, cid, module_id[7:], expected)
        auth.grant(db, cid, module["vault_id"], "original")
        figures = (module.get("catalog") or {}).get("figures", [])
        found = [f for f in figures if f.get("id") == resource_id]
        if len(found) != 1:
            return {**result, "status": "missing", "reason": "resource_not_registered"}
        figure = found[0]
        image = figure.get("image")
        name = (
            image
            if isinstance(image, str)
            else image.get("path")
            if isinstance(image, dict)
            else None
        )
        if not isinstance(name, str):
            return {**result, "status": "missing", "reason": "image_not_packaged"}
        media_type = mimetypes.guess_type(name)[0]
        if media_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            return {**result, "status": "unsupported", "reason": "image_format_unsupported"}
        raw = published_expert.member(config, db, cid, module_id[7:], expected, name, original=True)
        if len(raw) > 8 * 1024 * 1024:
            return {**result, "status": "unsupported", "reason": "image_capacity"}
        image_sha = image.get("sha256") if isinstance(image, dict) else figure.get("image_sha256")
        if image_sha and digest(raw) != image_sha:
            raise IntegrityError("knowledge_module_changed")
        return {
            **result,
            "status": "available",
            "media_type": media_type,
            "sha256": digest(raw),
            "data_base64": base64.b64encode(raw).decode(),
            "caption": str(figure.get("caption") or figure.get("label") or resource_id),
        }
    except auth.Denied:
        return {**result, "status": "unauthorized", "reason": "resource_permission_denied"}
    except FileNotFoundError:
        return {**result, "status": "missing", "reason": "resource_file_missing"}
    except (InvalidInput, IntegrityError) as error:
        return {**result, "status": "stale", "reason": str(error)}
