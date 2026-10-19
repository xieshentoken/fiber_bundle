"""MCP image content is client-readable; actual end-user rendering remains unconfirmed."""

import copy
from typing import Any

from .expert_presentation import (
    image_bytes,
    markdown_table,
    markdown_text,
    plain,
    reason,
    table_rows,
)


def result(packet: dict[str, Any], capability: str) -> Any:
    is_resource = packet.get("kind") == "expert_media" or "resource_id" in packet
    if capability not in {"images", "full"} or (
        not packet.get("expert_blocks") and not is_resource
    ):
        return packet
    from mcp.types import CallToolResult, ImageContent, TextContent

    structured = copy.deepcopy(packet)
    text = markdown_text(plain(packet))
    if is_resource:
        text = "[" + str(packet.get("resource_id") or "") + "] " + str(packet.get("caption") or "")
        if packet.get("status") != "available":
            text += "：" + reason(packet)
    content: list[Any] = [
        TextContent(
            type="text", text=markdown_text(text) if is_resource else text or "专家来源读取结果"
        )
    ]
    seen = set()
    for block in structured.get("expert_blocks", []):
        for item in block.get("media", []):
            encoded = item.pop("data_base64", None)
            key = (block["module_id"], block["version"], item["resource_id"])
            if (
                item.get("status") == "available"
                and item.get("render_as") == "table"
                and (rows := table_rows(item))
                and key not in seen
            ):
                content.append(
                    TextContent(
                        type="text",
                        text=markdown_text(
                            (item.get("caption") or "表格") + " · " + item["resource_id"]
                        )
                        + "\n\n"
                        + markdown_table(rows),
                    )
                )
                seen.add(key)
                continue
            if item.get("status") == "available" and encoded and key not in seen:
                image_bytes({**item, "data_base64": encoded})
                content.append(
                    TextContent(
                        type="text", text=markdown_text(item.get("caption") or item["resource_id"])
                    )
                )
                content.append(
                    ImageContent(type="image", data=encoded, mimeType=item["media_type"])
                )
                seen.add(key)
    if structured.get("kind") == "expert_media" or "resource_id" in structured:
        encoded = structured.pop("data_base64", None)
        if encoded and structured.get("status") == "available":
            image_bytes({**structured, "data_base64": encoded})
            content.append(
                ImageContent(type="image", data=encoded, mimeType=structured["media_type"])
            )
    structured["mcp_presentation"] = {
        "model_readable": bool(seen) or len(content) > 1,
        "user_visible": "unconfirmed",
        "client_capability": capability,
    }
    return CallToolResult(content=content, structuredContent=structured)
