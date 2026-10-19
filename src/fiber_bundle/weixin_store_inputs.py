"""Project verified journal bytes into Store assets without platform credentials."""

import json
import sqlite3
from typing import Any

from . import feishu_archives
from . import weixin_intents as intents
from . import weixin_recovery as recovery
from .config import InvalidInput
from .inputs import AssetInput
from .weixin_events import Binding


def source(
    db: sqlite3.Connection, binding: Binding, job_id: str, ordinal: int
) -> tuple[dict[str, Any], tuple[AssetInput, ...]]:
    row, media, _, state = recovery.load(db, binding, job_id)
    if state != "pending":
        raise InvalidInput("batch source incomplete or unsupported")
    message = json.loads(row["raw"])
    if binding.account_id.startswith("feishu:"):
        metadata = message.get("fiber_feishu")
        if not isinstance(metadata, dict) or metadata.get("schema") != "fiber-feishu-source/v1":
            raise InvalidInput("Feishu source provenance required")
        intent = intents.event_locked(db, row)
        keys = None if intent is None else intent["source_keys"]
        feishu_assets = []
        for key, value in enumerate(message["item_list"]):
            if keys is not None and str(key) not in keys:
                continue
            if value["type"] == 1:
                content = value["text_item"]["text"]
                if keys is not None:
                    start, end = keys[str(key)]
                    content = content[start:end]
                feishu_assets.append(
                    AssetInput(
                        content.encode(),
                        f"feishu-{ordinal}-{key}.txt",
                        "text/plain",
                        "body",
                    )
                )
            else:
                file = metadata["files"][str(key)]
                feishu_assets.extend(feishu_archives.assets(media[str(key)], file["name"]))
        return {
            "journal_job_id": job_id,
            "raw_sha256": row["raw_sha256"],
            "platform": "feishu",
            "raw_event": metadata["raw_event"],
            "platform_completeness": "not_verified",
            "items": [asset.descriptor() for asset in feishu_assets],
        }, tuple(feishu_assets)
    intent = intents.event_locked(db, row)
    assets = []
    locations = []

    def item(value: dict[str, Any], key: str) -> None:
        name = f"message-{ordinal}-item-{key}"
        keys = None if intent is None else intent["source_keys"]
        if keys is not None and key not in keys:
            if value.get("ref_msg") is not None:
                item(value["ref_msg"]["message_item"], key + ".ref")
            return
        if value["type"] == 1:
            text = value["text_item"]["text"]
            if keys is not None:
                start, end = keys[key]
                text = text[start:end]
            asset = AssetInput(text.encode(), name + ".txt", "text/plain", "body")
        else:
            # Original filename/MIME are untrusted platform hints; retain exact bytes.
            asset = AssetInput(media[key], name + ".bin", "application/octet-stream")
        assets.append(asset)
        locations.append({"item_key": key, **asset.descriptor()})
        if value.get("ref_msg") is not None:
            item(value["ref_msg"]["message_item"], key + ".ref")

    for index, value in enumerate(message["item_list"]):
        item(value, str(index))
    return {
        "journal_job_id": job_id,
        "raw_sha256": row["raw_sha256"],
        "items": locations,
    }, tuple(assets)
