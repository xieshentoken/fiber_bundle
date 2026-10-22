"""Deterministic material checks, retaining complete batch scope on rejection."""

import json
import sqlite3
from typing import Any

from ..config import IntegrityError
from ..objects import digest
from ..weixin_events import Binding
from . import intake_links

COMMANDS = ("查询本库", "查询：", "查询:", "查询[", "批次 ", "术语 ", "文档 ")


def classify(
    journal: sqlite3.Connection, bound: Binding, batch_id: str, settings: dict[str, Any]
) -> dict[str, Any]:
    found: list[dict[str, Any]] = []
    material: set[str] = set()
    cards: list[dict[str, str]] = []
    sent_times: list[float] = []
    rows = journal.execute(
        "SELECT e.rowid AS sequence,e.* FROM wx_batch_members m JOIN events e "
        "ON e.account_id=m.account_id AND e.event_id=m.event_id "
        "WHERE m.batch_id=? AND e.account_id=? ORDER BY m.ordinal",
        (batch_id, bound.account_id),
    ).fetchall()
    reasons: set[str] = set()
    expert_table = journal.execute(
        "SELECT 1 FROM sqlite_master WHERE name='fiber_expert_commands'"
    ).fetchone()
    for row in rows:
        if (
            expert_table
            and journal.execute(
                "SELECT 1 FROM fiber_expert_commands WHERE state NOT IN ('expired','cancelled') "
                "AND (json_extract(request_json,'$.intent.selected_file.event_id')=? "
                "OR json_extract(request_json,'$.intent.reply_message_id')=?)",
                (row["event_id"], row["event_id"]),
            ).fetchone()
        ):
            reasons.add("intake_expert_file_bound")
        raw = bytes(row["raw"])
        if digest(raw) != row["raw_sha256"]:
            raise IntegrityError("intake_source_digest_changed")
        message = json.loads(raw)
        stamp = (
            message.get("fiber_feishu", {})
            .get("raw_event", {})
            .get("event", {})
            .get("message", {})
            .get("create_time")
        )
        if stamp is not None:
            if isinstance(stamp, (str, int)) and str(stamp).isdigit() and int(stamp) > 0:
                sent_times.append(int(stamp) / 1000)
            else:
                reasons.add("intake_sent_time_unverified")
        card_kind = message.get("fiber_feishu", {}).get("card_kind")
        if card_kind:
            material.add("link")
            cards.append({"kind": card_kind, "content_state": "card_only"})
            if card_kind not in settings["sites"]:
                reasons.add("intake_site_not_allowed")
        for item in message["item_list"]:
            kind = item.get("type")
            if kind == 1:
                value = item["text_item"]["text"]
                if value.strip().startswith(COMMANDS):
                    reasons.add("intake_command_requires_routing")
                urls = intake_links.links(value)
                if urls:
                    material.add("link")
                    for url in urls:
                        permitted = intake_links.allowed(url, settings)
                        found.append({"original_url": url, "allowed": permitted})
                        if not permitted:
                            reasons.add("intake_site_not_allowed")
                elif not card_kind:
                    material.add("text")
            elif kind in {2, 3, 4, 5}:
                material.add("image" if kind == 2 else "file")
            else:
                reasons.add("intake_message_unsupported")
    if material - set(settings["materials"]):
        reasons.add("intake_material_not_selected")
    return {
        "reason": next(
            (
                code
                for code in (
                    "intake_expert_file_bound",
                    "intake_command_requires_routing",
                    "intake_message_unsupported",
                    "intake_sent_time_unverified",
                    "intake_material_not_selected",
                    "intake_site_not_allowed",
                )
                if code in reasons
            ),
            None,
        ),
        "links": found,
        "materials": sorted(material),
        "first_row": min((r["sequence"] for r in rows), default=0),
        "last_row": max((r["sequence"] for r in rows), default=0),
        "event_ids": [r["event_id"] for r in rows],
        "cards": cards,
        "oldest_sent_at": min(sent_times) if sent_times else None,
    }
