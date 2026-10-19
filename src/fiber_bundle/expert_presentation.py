"""Deterministic expert-only output negotiation and portable Markdown export; no models."""

import base64
import binascii
import copy
import hashlib
import io
import re
import zipfile
from collections.abc import Callable
from typing import Any

from .config import InvalidInput

MIMES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
LIMIT = 8 * 1024 * 1024
REASONS = {
    "output_capability_unknown": "出口图表能力未知，保留文字",
    "output_text_only": "出口仅支持文字",
    "output_table_unsupported": "出口不支持表格，且没有可用原图；已有文字保留",
    "resource_permission_denied": "无图表读取权限",
    "resource_not_registered": "图表资源未登记",
    "image_not_packaged": "图表未包含原图",
    "resource_file_missing": "图表文件缺失",
    "image_format_unsupported": "图片格式不支持",
    "image_capacity": "图片超出出口容量",
    "resource_read_failed": "图表读取失败",
    "resource_binding_changed": "图表版本或关联已变化",
    "resource_integrity_invalid": "图表内容校验失败",
    "image_upload_failed": "图表上传失败",
    "image_upload_unknown": "图表上传结果未知，未重试",
}


def reason(item: dict[str, Any]) -> str:
    return REASONS.get(
        str(item.get("reason") or ""),
        {
            "unauthorized": "无图表读取权限",
            "missing": "图表资源缺失",
            "unsupported": "出口不支持该图表",
            "stale": "图表版本已变化",
        }.get(str(item.get("status") or ""), "图表不可用"),
    )


def image_bytes(item: dict[str, Any]) -> bytes:
    if item.get("media_type") not in MIMES:
        raise InvalidInput("image_format_unsupported")
    encoded = item.get("data_base64")
    if not isinstance(encoded, str) or len(encoded) > (LIMIT + 2) // 3 * 4:
        raise InvalidInput("image_capacity")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise InvalidInput("resource_integrity_invalid") from error
    if not raw or len(raw) > LIMIT or hashlib.sha256(raw).hexdigest() != item.get("sha256"):
        raise InvalidInput("resource_integrity_invalid")
    return raw


def table_rows(item: dict[str, Any]) -> list[list[str]] | None:
    """Only a reader-verified rectangular grid is eligible; captions/OCR are not a grid."""
    rows = item.get("rows")
    if (
        item.get("rows_verified") is not True
        or not isinstance(rows, list)
        or not 2 <= len(rows) <= 200
    ):
        return None
    if not all(isinstance(row, list) and 1 <= len(row) <= 30 for row in rows):
        return None
    width = len(rows[0])
    if any(len(row) != width for row in rows) or any(
        not isinstance(cell, (str, int, float)) or isinstance(cell, bool)
        for row in rows
        for cell in row
    ):
        return None
    return [[str(cell) for cell in row] for row in rows]


def project(
    response: dict[str, Any],
    capability: str = "unknown",
    reader: Callable[[str, str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if capability not in {"unknown", "text", "images", "full"}:
        raise InvalidInput("expert_output_capability_invalid")
    if not response.get("expert_blocks"):
        return response  # Ordinary notes never read images or gain media placeholders.
    result = copy.deepcopy(response)
    cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    shown = total = 0
    for block in result["expert_blocks"]:
        for item in block.get("media", []):
            total += 1
            item.pop("data_base64", None)
            for field in ("rows", "rows_verified", "render_as"):
                item.pop(field, None)
            if capability in {"unknown", "text"}:
                item.update(
                    status="unsupported",
                    reason=(
                        "output_capability_unknown"
                        if capability == "unknown"
                        else "output_text_only"
                    ),
                )
                continue
            key = (block["module_id"], block["version"], item["resource_id"])
            if key not in cache:
                try:
                    value = (
                        reader(*key)
                        if reader
                        else {
                            "module_id": key[0],
                            "version": key[1],
                            "resource_id": key[2],
                            "status": "missing",
                            "reason": "resource_read_failed",
                        }
                    )
                    if any(
                        value.get(k) != v
                        for k, v in zip(("module_id", "version", "resource_id"), key, strict=True)
                    ):
                        value = {"status": "stale", "reason": "resource_binding_changed"}
                    if value.get("status") == "available":
                        if capability == "full" and table_rows(value):
                            value = {**value, "render_as": "table"}
                        else:
                            if table_rows(value) and not value.get("data_base64"):
                                raise InvalidInput("output_table_unsupported")
                            image_bytes(value)
                            value = {**value, "render_as": "image"}
                    cache[key] = value
                except InvalidInput as error:
                    cache[key] = {"status": "unsupported", "reason": str(error)}
                except (OSError, ValueError, TypeError, KeyError):
                    cache[key] = {"status": "missing", "reason": "resource_read_failed"}
            item.update(cache[key])
            shown += item.get("status") == "available"
    state = (
        "unknown"
        if capability == "unknown"
        else "text_only"
        if capability == "text"
        else (
            "fully_supported" if shown == total else "partially_supported" if shown else "text_only"
        )
    )
    result["presentation"] = {
        "capability": capability,
        "state": state,
        "media_count": total,
        "available_count": shown,
        "user_visible": "unconfirmed",
    }
    return result


def plain(response: dict[str, Any]) -> str:
    parts = [str(response.get("answer") or "")]
    for source in response.get("sources", []):
        parts.append(
            " ".join(
                str(source.get(k) or "") for k in ("citation", "title", "note_path", "snippet")
            )
        )
    for quotation in response.get("quotations", []):
        quote = str(quotation.get("quote") or "")
        if quote and not any(quote in part for part in parts):
            parts.append(str(quotation.get("citation") or "") + " " + quote)
    for block in response.get("expert_blocks", []):
        parts += [
            str(block.get("text") or ""),
            "专家 Draft · 审核待完成 · 知识未核实",
            "来源：" + block["module_id"] + " @ " + block["version"],
        ]
        if block.get("citation"):
            parts.append(str(block["citation"]))
        parts.append("证据 ID：" + " · ".join(block.get("evidence_ids", [])))
        parts.append("对象 ID：" + " · ".join(block.get("object_ids", [])))
        if block.get("behavior") == "abstain":
            parts.append("文字证据不足，保持弃答；图表展示不构成事实或数值核实。")
        for item in block.get("media", []):
            if item.get("status") != "available":
                parts.append(f"[{item['resource_id']}] {item.get('caption', '')}：{reason(item)}")
    return "\n\n".join(p for p in parts if p)


def markdown_text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>()|#!])", r"\\\1", value).replace("\r", "")


def markdown_table(rows: list[list[str]]) -> str:
    escaped = [[markdown_text(c).replace("\n", " ") for c in row] for row in rows]
    return "\n".join(
        ["| " + " | ".join(escaped[0]) + " |", "| " + " | ".join(["---"] * len(rows[0])) + " |"]
        + ["| " + " | ".join(row) + " |" for row in escaped[1:]]
    )


def markdown_archive(response: dict[str, Any]) -> bytes:
    """Export only already authorized, explicitly linked media into a self-contained ZIP."""
    parts = [markdown_text(str(response.get("answer") or ""))]
    assets: dict[str, bytes] = {}
    for source in response.get("sources", []):
        parts.append(
            markdown_text(
                " ".join(
                    str(source.get(k) or "") for k in ("citation", "title", "note_path", "snippet")
                )
            )
        )
    for block in response.get("expert_blocks", []):
        parts += [
            "## 专家 Draft · 审核待完成 · 知识未核实",
            markdown_text(block.get("text", "")),
            markdown_text(block["module_id"] + " @ " + block["version"]),
        ]
        if block.get("citation"):
            parts.append(markdown_text(str(block["citation"])))
        parts.append(markdown_text("证据 ID：" + " · ".join(block.get("evidence_ids", []))))
        parts.append(markdown_text("对象 ID：" + " · ".join(block.get("object_ids", []))))
        if block.get("behavior") == "abstain":
            parts.append("文字证据不足，保持弃答；展示图表不代表数值已核实。")
        for item in block.get("media", []):
            caption = markdown_text(item.get("caption") or item["resource_id"])
            if item.get("status") != "available":
                parts.append(f"{caption}：{reason(item)}")
            elif item.get("render_as") == "table" and (rows := table_rows(item)):
                parts.append(caption + "\n\n" + markdown_table(rows))
            else:
                raw = image_bytes(item)
                name = "assets/" + item["sha256"] + "." + MIMES[item["media_type"]]
                assets[name] = raw
                parts.append(f"![{caption}]({name})\n\n" + markdown_text(item["resource_id"]))
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("answer.md", "\n\n".join(p for p in parts if p) + "\n")
        for name, raw in assets.items():
            archive.writestr(name, raw)
    return result.getvalue()


def deliver(config: Any, db: Any, cid: str, result: dict[str, Any], request: Any) -> dict[str, Any]:
    from . import expert_media

    if request.output_capability == "unknown" and request.output_format == "json":
        return result
    value = project(
        result,
        request.output_capability,
        lambda module, version, resource: expert_media.read(
            config, db, cid, module, version, resource
        ),
    )
    if request.output_format == "markdown":
        value = {
            **value,
            "markdown_archive_base64": base64.b64encode(markdown_archive(value)).decode(),
        }
    return value


def bindings(config: Any, db: Any, cid: str, response: dict[str, Any]) -> dict[str, str]:
    """Reauthorize cached media without rereading binary, including move/rebind identity."""
    from . import auth, publication_locations, published_expert
    from .inputs import canonical

    result = {}
    for block in response.get("expert_blocks", []):
        if not any(m.get("status") == "available" for m in block.get("media", [])):
            continue
        module = published_expert.text(config, db, cid, block["module_id"][7:], block["version"])
        auth.grant(db, cid, module["vault_id"], "original")
        location = publication_locations.get(db, "expert", block["module_id"][7:])
        result[block["module_id"]] = canonical(
            {
                "version": module["version"],
                "vault_id": module["vault_id"],
                "entry": module["entry_note_path"],
                "source": module["source_version_id"],
                "package": module["package_sha256"],
                "location": location,
            }
        )
    return result
