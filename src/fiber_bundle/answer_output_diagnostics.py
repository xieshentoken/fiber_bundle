"""Content-free schema issues for the strict extractive selection contract."""

from pydantic import ValidationError

KINDS = frozenset(
    {
        "missing",
        "extra_forbidden",
        "model_type",
        "list_type",
        "string_type",
        "literal_error",
        "string_too_short",
        "string_too_long",
        "too_long",
    }
)


def field_path(location: tuple[str | int, ...]) -> str:
    """Never emit a model-authored field name or list index."""
    if location == ("mode",):
        return "$.mode"
    if location and location[0] == "quotations":
        if len(location) > 1 and type(location[1]) is int:
            if len(location) == 3 and location[2] in {"citation", "quote"}:
                return "$.quotations[]." + str(location[2])
            return "$.quotations[]"
        return "$.quotations"
    return "$"


def input_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (int, float)):
        return "number"
    return "other"


def schema_issues(error: ValidationError) -> list[dict[str, str]]:
    """Export only bounded, deduplicated vocabulary; discard messages, input and context."""
    issues: list[dict[str, str]] = []
    for item in error.errors(include_url=False, include_context=False):
        issue = {
            "path": field_path(item["loc"]),
            "kind": item["type"] if item["type"] in KINDS else "invalid",
            "input_type": "absent" if item["type"] == "missing" else input_type(item["input"]),
        }
        if issue not in issues:
            issues.append(issue)
        if len(issues) == 32:
            break
    return issues
