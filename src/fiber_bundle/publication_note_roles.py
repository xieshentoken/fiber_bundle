"""Reviewed source parts and derived analysis have distinct publication roles."""

import json

from .config import IntegrityError
from .inputs import canonical
from .objects import digest

MANIFEST = "publication-notes.json"
SUPPLEMENTS_SCHEMA = "fiber-note-supplements/v1"


def analysis_manifest() -> bytes:
    return canonical(
        {"schema": SUPPLEMENTS_SCHEMA, "supplements": [{"path": "analysis.md", "role": "analysis"}]}
    ).encode()


def roles(files: dict[str, bytes]) -> tuple[set[str], dict[str, str]]:
    """Only explicit role declarations authorize extra readable Markdown notes."""
    body = {"note.md"}
    supplements: dict[str, str] = {}
    if MANIFEST not in files:
        return body, supplements
    try:
        descriptor = json.loads(files[MANIFEST])
        if descriptor["schema"] == SUPPLEMENTS_SCHEMA:
            if set(descriptor) != {"schema", "supplements"}:
                raise ValueError("unexpected fields")
            entries = descriptor["supplements"]
            if not isinstance(entries, list) or len(entries) != 1:
                raise ValueError("one analysis expected")
            entry = entries[0]
            if entry != {"path": "analysis.md", "role": "analysis"}:
                raise ValueError("unsupported supplement")
            supplements[entry["path"]] = entry["role"]
        elif descriptor["schema"] == "fiber-document-notes/v1":
            body.update(p["path"] for p in descriptor["parts"])
            body.add(descriptor["catalog"]["path"])
            # Compatibility for unfinished older intake Pi captures, never arbitrary parts.
            if (
                descriptor
                == {
                    "schema": "fiber-document-notes/v1",
                    "parts": [{"path": "analysis.md"}],
                    "catalog": {"path": "note.md"},
                }
                and "analysis-provenance.json" in files
            ):
                provenance = json.loads(files["analysis-provenance.json"])
                if (
                    provenance.get("schema") == "fiber-intake-analysis/v1"
                    and provenance.get("source_sha256") == digest(files["note.md"])
                    and provenance.get("knowledge_verified") is False
                ):
                    body.remove("analysis.md")
                    supplements["analysis.md"] = "analysis"
        else:
            raise ValueError("unknown schema")
        if any(
            n not in files or "/" in n or "\\" in n or n.startswith(".") or not n.endswith(".md")
            for n in body | supplements.keys()
        ):
            raise ValueError("invalid note path")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise IntegrityError("layout_part_manifest_invalid") from exc
    return body, supplements


def navigation(raw: bytes, logical: str, supplements: dict[str, str]) -> bytes:
    """Add navigation in the controlled projection; reviewed source bytes stay intact."""
    if not supplements:
        return raw
    if logical == "note.md":
        link = "[Pi + Skill 分析](analysis.md)"
    elif supplements.get(logical) == "analysis":
        link = "[原文笔记](note.md)"
    else:
        return raw
    return raw + ("\n\n---\n\n" + link + "\n").encode()
