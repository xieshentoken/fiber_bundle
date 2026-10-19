"""Byte-bound page locators, including legacy published native-PDF headings."""

import json
import re
from typing import Any

from ..config import InvalidInput


def legacy_pages(text: str) -> list[dict[str, Any]]:
    """Only called for unchanged, manifest-bound published documents, never arbitrary notes.

    Old native-PDF publications have typed page headings but no stored byte map.
    Ignore source headings inside the renderer's fences; never inspect private candidates.
    """
    units = []
    active = None
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None and line.startswith("## "):
            if active:
                units.append({**active, "rendered_end": offset})
                active = None
            if line.startswith("## {") and len(line) < 512:
                try:
                    value = json.loads(line[3:])
                except ValueError:
                    value = {}
                if (
                    isinstance(value, dict)
                    and set(value) == {"physical_page", "pdf_page_label"}
                    and type(value["physical_page"]) is int
                    and 1 <= value["physical_page"] <= 100000
                ):
                    active = {"locator": value, "rendered_start": offset}
        offset += len(line.encode())
    if active:
        units.append({**active, "rendered_end": offset})
    return units


def page_ranges(text: str, provenance: dict[str, Any], page: int) -> list[tuple[int, int]]:
    spans = provenance.get("document_source_spans", {})
    part = provenance.get("document_part", {})
    if spans.get("matches_published"):
        units, origin = spans.get("published_units") or [], 0
    elif part.get("matches_reviewed_extraction"):
        units, origin = part.get("source_units", []), part["rendered_start"]
    elif provenance.get("source_kind") == "published_document" and not part:
        units, origin = legacy_pages(text), 0
    else:
        raise InvalidInput("knowledge_evidence_locator")
    ranges = [
        (u["rendered_start"] - origin, u["rendered_end"] - origin)
        for u in units
        if u["locator"].get("physical_page") == page
    ]
    if not ranges:
        raise InvalidInput("knowledge_evidence_locator")
    return ranges
