"""Bounded search projection for external Agents; provenance is host-owned."""

import json
import re
from typing import Any

from ..config import InvalidInput
from ..inputs import canonical, text

KINDS = frozenset(
    {
        "historical_note",
        "published_article",
        "published_document",
        "published_media",
        "published_channel_content",
        "user_revision",
        "agent_analysis",
    }
)


def project(value: object, vault_ids: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    """Accept only the Vault-note response, never session or original-download results."""
    if not isinstance(value, dict):
        raise InvalidInput("agent_search_envelope")
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 262144:
        raise InvalidInput("agent_search_envelope")
    data = json.loads(encoded)
    rows = data.get("results")
    if (
        data.get("scope") != "vault_notes"
        or data.get("searched_vault_ids") != list(vault_ids)
        or data.get("status") not in {"evidence_found", "no_evidence"}
        or not isinstance(rows, list)
        or len(rows) > limit
        or (data["status"] == "evidence_found") != bool(rows)
    ):
        raise InvalidInput("agent_search_scope_or_status")
    result = []
    for ordinal, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise InvalidInput("agent_search_item")
        if row.get("vault_id") not in vault_ids or row.get("citation") != f"[{ordinal}]":
            raise InvalidInput("agent_search_identity")
        sha, start, end, snippet = (row.get(k) for k in ("sha256", "line", "line_end", "snippet"))
        if (
            not isinstance(sha, str)
            or re.fullmatch(r"[a-f0-9]{64}", sha) is None
            or type(start) is not int
            or type(end) is not int
            or not 1 <= start <= end
            or not isinstance(snippet, str)
            or not snippet.strip()
            or len(snippet) > 16000
            or start + len(snippet.splitlines()) - 1 != end
        ):
            raise InvalidInput("agent_search_location")
        title = text(row.get("title"), limit=1024)
        path = text(row.get("note_path"), limit=4096)
        if (
            path.startswith("/")
            or "\\" in path
            or any(p in {"", ".", ".."} for p in path.split("/"))
        ):
            raise InvalidInput("agent_search_path")
        kind = row.get("source_kind")
        if kind not in KINDS:
            raise InvalidInput("agent_search_provenance")
        label = text(row.get("source_label"), limit=1000)
        independent = row.get("independent_evidence", False)
        if type(independent) is not bool or (kind == "agent_analysis" and independent):
            raise InvalidInput("agent_search_provenance")
        item = {
            "vault_id": row["vault_id"],
            "note_path": path,
            "title": title,
            "sha256": sha,
            "line": start,
            "line_end": end,
            "snippet": snippet,
            "source_kind": kind,
            "source_label": label,
            "independent_evidence": independent,
        }
        for key in ("note_id", "source_version_id", "source_url"):
            field = row.get(key)
            if field is not None:
                item[key] = text(field, limit=4096)
        expert_fields = {"expert_module_id", "expert_module_version", "expert_projection"}
        if expert_fields & row.keys():
            if (
                not expert_fields <= row.keys()
                or not isinstance(row["expert_module_id"], str)
                or not row["expert_module_id"].startswith("expert:")
                or len(row["expert_module_id"]) > 200
                or not isinstance(row["expert_module_version"], str)
                or re.fullmatch(r"[a-f0-9]{64}", row["expert_module_version"]) is None
                or type(row["expert_projection"]) is not bool
                or row["expert_projection"]
                and kind != "agent_analysis"
            ):
                raise InvalidInput("agent_search_provenance")
            item.update({key: row[key] for key in expert_fields})
        # Preserve qualified extraction/expert provenance rather than upgrading it to source fact.
        if "document_part" in row:
            part = row["document_part"]
            if not isinstance(part, dict) or len(canonical(part).encode()) > 8192:
                raise InvalidInput("agent_search_provenance")
            item["document_part"] = part
        quality = row.get("completeness_status", "unknown")
        if quality not in {"passed", "partial", "unknown", "not_checked"}:
            raise InvalidInput("agent_search_provenance")
        item["completeness_status"] = quality
        gaps = row.get("known_gaps", [])
        if (
            not isinstance(gaps, list)
            or len(gaps) > 2000
            or any(not isinstance(g, str) or len(g) > 4000 for g in gaps)
        ):
            raise InvalidInput("agent_search_provenance")
        item["known_gaps"] = gaps
        if row.get("save_basis") is not None:
            item["save_basis"] = text(row["save_basis"], limit=200)
        for key in ("artifact_stage", "review_state"):
            if key in row:
                item[key] = text(row[key], limit=100)
        if "knowledge_verified" in row:
            if type(row["knowledge_verified"]) is not bool or (
                row["knowledge_verified"]
                and (kind == "agent_analysis" or row.get("artifact_stage") == "draft")
            ):
                raise InvalidInput("agent_search_provenance")
            item["knowledge_verified"] = row["knowledge_verified"]
        result.append(item)
    return result


def merge(batches: list[list[dict[str, Any]]], max_chars: int) -> list[dict[str, Any]]:
    """Deduplicate exact passages; differing snapshots of one note fail as a whole."""
    result: list[dict[str, Any]] = []
    versions: dict[tuple[str, str], str] = {}
    seen: dict[str, int] = {}
    size = 0
    for query_index, batch in enumerate(batches, 1):
        for ordinal, item in enumerate(batch, 1):
            key = (item["vault_id"], item["note_path"])
            previous = versions.setdefault(key, item["sha256"])
            if previous != item["sha256"]:
                raise InvalidInput("agent_search_source_changed")
            encoded = canonical(item)
            match = {"query_index": query_index, "citation": f"[{ordinal}]"}
            if encoded in seen:
                result[seen[encoded]]["retrieval_matches"].append(match)
                continue
            seen[encoded] = len(result)
            size += len(item["snippet"])
            if size > max_chars:
                raise InvalidInput("agent_evidence_limit")
            result.append(
                {**item, "citation": f"[{len(result) + 1}]", "retrieval_matches": [match]}
            )
    return result
