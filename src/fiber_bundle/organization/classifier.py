"""Deterministic organizer plans. Ambiguous/title-only matches require human review."""

from typing import Any

from ..inputs import canonical
from ..objects import digest
from ..vault_scope import inside
from . import engine, text_features
from .catalog import manual_memberships
from .contracts import ORGANIZER_VERSION


def condition(entity: Any, item: Any) -> bool:
    values = item["values"]
    field = item["field"]
    if field == "path":
        return any(inside(entity["path"], v.rstrip("/")) for v in values)
    if field in {"title", "body"}:
        text = entity["title"] if field == "title" else entity["text"]
        return any(value.casefold() in text.casefold() for value in values)
    if field == "tag_prefix":
        return any(
            tag.casefold().startswith(value.casefold())
            for tag in entity.get("tags", [])
            for value in values
        )
    if field == "tag":
        return bool(set(entity.get("tags", [])) & set(values))
    return entity.get(field) in values


def matches(entity: Any, match: str, conditions: Any) -> bool:
    values = [condition(entity, c) for c in conditions]
    return not values or (all(values) if match == "all" else any(values))


def eligible(entity: Any, policy: Any) -> bool:
    return (
        entity["kind"] in policy["kinds"]
        and any(inside(entity["path"], p) for p in policy["include"])
        and not any(inside(entity["path"], p) for p in policy["exclude"])
        and matches(entity, policy["match"], policy["conditions"])
    )


def plan(entity: Any, mocs: Any, policy: Any, decisions: Any, context: Any) -> Any:
    manual = manual_memberships(entity, mocs)
    relevant = [d for d in decisions if d["entity"] == entity["id"]]
    fingerprint = digest(
        canonical(
            {
                "entity": {
                    **{k: v for k, v in entity.items() if k != "status" and not k.startswith("_")},
                    **entity.get("_classification_source", {}),
                },
                "mocs": context,
                "policy": policy,
                "decisions": relevant,
                "organizer": ORGANIZER_VERSION,
            }
        ).encode()
    )
    candidates = {}
    evidence = {}
    for rule in policy["rules"]:
        if matches(entity, rule["match"], rule["conditions"]):
            candidates[rule["moc_id"]] = 100
            evidence[rule["moc_id"]] = "explicit_rule"
    for candidate in engine.infer(entity, mocs):
        mid = candidate["hub"]
        if candidate["score"] > candidates.get(mid, 0):
            candidates[mid] = candidate["score"]
            evidence[mid] = "; ".join(candidate["reasons"])
    for mid in manual:
        candidates[mid] = 100
        evidence[mid] = "existing_manual"
    allowed = {m["id"] for m in mocs}
    rejected = {d["moc"] for d in relevant if d["state"] in {"rejected", "retired"}}
    pinned = {d["moc"] for d in relevant if d["state"] == "pinned"}
    selected = sorted(
        (({m for m, s in candidates.items() if s >= 70} | set(manual) | pinned) - rejected)
        & allowed
    )
    suggestions = [
        {"moc_id": mid, "score": score, "reason": evidence[mid]}
        for mid, score in sorted(candidates.items(), key=lambda p: -p[1])
        if mid in allowed and mid not in rejected
    ]
    return {
        "entity": entity["id"],
        "path": entity["path"],
        "title": entity["title"],
        "kind": entity["kind"],
        "sha256": entity["sha256"],
        "fingerprint": fingerprint,
        "selected": selected,
        "manual": manual,
        "candidates": suggestions,
        "status": "linked" if selected else "review" if suggestions else "unclassified",
        "organizer_version": ORGANIZER_VERSION,
        "model_called": False,
        "rejected": sorted(rejected),
        "evaluated": not selected
        and len(
            "".join(
                p["text"] for p in text_features.passages(entity["text"]) if p["kind"] == "body"
            )
        )
        < 20,
    }
