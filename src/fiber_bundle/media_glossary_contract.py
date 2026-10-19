"""Literal terminology snapshots and conservative ambiguity detection; no corrections."""

import json
import unicodedata
from typing import Any

from .config import InvalidInput
from .media_files import canonical

MAX_BYTES = 64 * 1024
MAX_TERMS = 100
MAX_REVISIONS = 100


def text(value: object, maximum: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not value and not empty)
        or value.strip() != value
        or any(unicodedata.category(c) in {"Cc", "Cf"} for c in value)
    ):
        raise InvalidInput("media_glossary_text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise InvalidInput("media_glossary_unicode") from exc
    return value


def key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def fields(value: object, expected: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise InvalidInput("media_glossary_fields")
    return value


def validate(value: object) -> dict[str, Any]:
    result = fields(value, {"schema", "topic", "terms", "unresolved"})
    if result["schema"] != "fiber-media-glossary/v1":
        raise InvalidInput("media_glossary_schema")
    text(result["topic"], 200, empty=True)
    if not isinstance(result["terms"], list) or len(result["terms"]) > MAX_TERMS:
        raise InvalidInput("media_glossary_terms")
    seen = set()
    for entry in result["terms"]:
        term = fields(
            entry,
            {
                "canonical",
                "full_name",
                "chinese_name",
                "preserve_case",
                "context_keywords",
                "aliases",
            },
        )
        identity = key(text(term["canonical"], 100))
        if identity in seen:
            raise InvalidInput("media_glossary_duplicate_term")
        seen.add(identity)
        text(term["full_name"], 200, empty=True)
        text(term["chinese_name"], 200, empty=True)
        if type(term["preserve_case"]) is not bool:
            raise InvalidInput("media_glossary_case")
        keywords = term["context_keywords"]
        if not isinstance(keywords, list) or len(keywords) > 10:
            raise InvalidInput("media_glossary_context")
        if len({key(text(v, 100)) for v in keywords}) != len(keywords):
            raise InvalidInput("media_glossary_duplicate_context")
        aliases = term["aliases"]
        if not isinstance(aliases, list) or len(aliases) > 10:
            raise InvalidInput("media_glossary_aliases")
        alias_seen = set()
        for item in aliases:
            alias = fields(item, {"value", "mode"})
            name = key(text(alias["value"], 100))
            if name in alias_seen or name == identity:
                raise InvalidInput("media_glossary_duplicate_alias")
            alias_seen.add(name)
            if alias["mode"] not in ("safe", "contextual"):
                raise InvalidInput("media_glossary_alias_mode")
    unresolved = result["unresolved"]
    if not isinstance(unresolved, list) or len(unresolved) > 100:
        raise InvalidInput("media_glossary_unresolved")
    for item in unresolved:
        issue = fields(item, {"term", "reason"})
        text(issue["term"], 100)
        text(issue["reason"], 200)
    if len(canonical(result)) > MAX_BYTES:
        raise InvalidInput("media_glossary_size")
    return result


def load(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise InvalidInput("media_glossary_size")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for name, value in items:
            if name in result:
                raise InvalidInput("media_glossary_duplicate_field")
            result[name] = value
        return result

    def constant(value: str) -> None:
        raise InvalidInput("media_glossary_nonfinite")

    try:
        return validate(
            json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidInput("media_glossary_json") from exc


def ambiguities(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    targets: dict[str, set[str]] = {}
    labels: dict[str, str] = {}
    missing_context = []
    for term in snapshot["terms"]:
        words = [term["canonical"], *(a["value"] for a in term["aliases"])]
        for word in words:
            identity = key(word)
            targets.setdefault(identity, set()).add(term["canonical"])
            labels[identity] = word
        for alias in term["aliases"]:
            if alias["mode"] == "contextual" and not term["context_keywords"]:
                missing_context.append({"term": alias["value"], "reason": "context_missing"})
    return (
        [
            {"term": labels[name], "reason": "multiple_targets", "targets": sorted(values)}
            for name, values in sorted(targets.items())
            if len(values) > 1
        ]
        + missing_context
        + list(snapshot["unresolved"])
    )
