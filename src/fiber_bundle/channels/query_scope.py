"""Explicit per-message Vault selection, independent of the material ingress binding."""

import hashlib
import re
from typing import Any

from pydantic import ValidationError

from .. import auth, vaults
from ..config import Config, InvalidInput
from ..inputs import canonical, text
from ..payloads import Identifier, Payload

ALIAS = re.compile(r"[\w\-\u3400-\u9fff]{1,32}\Z")
RESERVED = {"本库", "原库", "主库", "当前", "默认", "全部"}
COMMAND = re.compile(r"^查询(?:\[([^\[\]\r\n]+)\])?\s*[:：]\s*(.*)$", re.S)
LEGACY = ("查询本库",)
SCHEMA = "channel-query-scope/v1"
KEY = "query_scope"


class QueryVault(Payload):
    vault_id: Identifier
    alias: str


def entries(value: dict[str, Any]) -> list[dict[str, str]]:
    if "query_vaults" not in value:
        return [{"vault_id": value["vault_id"], "alias": ""}]
    selected = value["query_vaults"]
    if (
        not isinstance(selected, list)
        or not 1 <= len(selected) <= 20
        or value.get("add")
        or value.get("purpose") == "intake"
    ):
        raise InvalidInput("channel_query_scope_invalid")
    result = []
    ids: set[str] = set()
    aliases: set[str] = set()
    for item in selected:
        try:
            parsed = QueryVault.model_validate(item).model_dump()
        except ValidationError as error:
            raise InvalidInput("channel_query_scope_invalid") from error
        alias, vid = parsed["alias"], parsed["vault_id"]
        if (
            not ALIAS.fullmatch(alias)
            or alias in RESERVED
            or alias.casefold() in aliases
            or vid in ids
        ):
            raise InvalidInput("channel_query_scope_invalid")
        ids.add(vid)
        aliases.add(alias.casefold())
        result.append(parsed)
    return result


def configured_ids(value: dict[str, Any]) -> list[str]:
    return [item["vault_id"] for item in entries(value)]


def referenced(value: dict[str, Any]) -> set[str]:
    return {value["vault_id"], *configured_ids(value)}


def signature(channel: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical({"revision": channel["revision"], "vaults": entries(channel["value"])}).encode()
    ).hexdigest()


def snapshot(channel: dict[str, Any], alias: str | None = None) -> dict[str, Any]:
    selected = entries(channel["value"])
    if alias is not None:
        selected = [item for item in selected if item["alias"] == alias]
        if not selected:
            raise InvalidInput("channel_query_alias_unknown")
    return {"schema": SCHEMA, "configuration_sha256": signature(channel), "vaults": selected}


def selected(channel: dict[str, Any]) -> dict[str, Any]:
    return channel.get("_query_scope") or snapshot(channel)


def bind(channel: dict[str, Any], scope: Any) -> dict[str, Any]:
    if scope is None:
        if "query_vaults" in channel["value"]:
            raise InvalidInput("channel_query_scope_changed")
        scope = snapshot(channel)
    if (
        not isinstance(scope, dict)
        or scope.get("schema") != SCHEMA
        or scope.get("configuration_sha256") != signature(channel)
        or not isinstance(scope.get("vaults"), list)
        or not scope["vaults"]
        or len(scope["vaults"]) != len({canonical(v) for v in scope["vaults"]})
        or any(v not in entries(channel["value"]) for v in scope["vaults"])
    ):
        raise InvalidInput("channel_query_scope_changed")
    return {**channel, "_query_scope": scope}


def checked(config: Config, db: Any, channel: dict[str, Any]) -> list[Any]:
    from ..vault_relocation import guard

    scope = selected(channel)
    bind(channel, scope)
    result = []
    for item in scope["vaults"]:
        vault = auth.grant(db, channel["value"]["client_id"], item["vault_id"], "query")
        guard.check(db, item["vault_id"])
        vaults.checked_vault(config, vault)
        result.append(vault)
    return result


def parse(
    body: str, natural: bool, *, standalone: bool = False
) -> tuple[str | None, str | None, str | None]:
    """Return question, explicit alias, or a finite control diagnostic; never guess a Vault."""
    value = body.strip()
    if value.startswith(LEGACY):
        return None, None, "channel_query_command_removed"
    match = COMMAND.fullmatch(value)
    if match:
        alias, question = match.groups()
        if not question.strip():
            return None, None, "channel_query_question_required"
        return text(question.strip(), limit=2000), alias, None
    if re.match(r"^查询\s*[\[【（(]", value) or re.match(r"^查询[^:：\r\n]*[:：]", value):
        return None, None, "channel_query_command_invalid"
    from .natural_query import question as natural_question

    return natural_question(value, natural, standalone=standalone), None, None


def hint(channel: dict[str, Any], code: str) -> dict[str, Any]:
    messages = {
        "channel_query_command_removed": (
            "“查询本库”已取消。请使用“查询：问题”或“查询[库别名]：问题”。"
        ),
        "channel_query_alias_unknown": "该库别名未绑定或已失效，没有改查其他库。",
        "channel_query_question_required": "请在冒号后填写问题。",
        "channel_query_command_invalid": (
            "查询命令格式不正确。请使用“查询：问题”或“查询[库别名]：问题”。"
        ),
        "channel_query_scope_unavailable": (
            "本次所选知识库不可用或权限不足，没有缩小查询范围。请核对知识库连接、索引与权限。"
        ),
    }
    aliases = [v["alias"] for v in entries(channel["value"]) if v["alias"]]
    answer = messages[code]
    if aliases:
        answer += " 可用命令：" + "、".join("查询[" + a + "]：问题" for a in aliases)
    return {"status": "query_hint", "answer": answer, "sources": [], "error_code": code}


def describe(config: Config, db: Any, channel: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in entries(channel["value"]):
        row = db.execute("SELECT name FROM vaults WHERE id=?", (item["vault_id"],)).fetchone()
        try:
            checked(config, db, bind(channel, snapshot(channel, item["alias"] or None)))
            available = True
        except (auth.Denied, InvalidInput):
            available = False
        rows.append({**item, "name": row["name"] if row else "—", "available": available})
    return {"default": "all", "vaults": rows, "explicit": "query_vaults" in channel["value"]}


def annotate(config: Config, db: Any, channel: dict[str, Any], result: dict[str, Any]) -> None:
    scope = selected(channel)
    names = {v["id"]: v["name"] for v in checked(config, db, channel)}
    labels = {v["vault_id"]: v["alias"] or names[v["vault_id"]] for v in scope["vaults"]}
    result[KEY] = scope
    result["query_scope_labels"] = list(labels.values())
    result["vault_name"] = "、".join(labels.values())
    for source in result.get("sources", []):
        source["vault_name"] = names[source["vault_id"]]
        source["vault_alias"] = labels[source["vault_id"]]
