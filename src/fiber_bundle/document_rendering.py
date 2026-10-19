"""Literal reviewed-document rendering; source-unit spans address rendered UTF-8 bytes."""

import base64
import json
import re
import sqlite3
from typing import Any

from .config import InvalidInput
from .inputs import canonical


def render(
    metadata: dict[str, Any], candidate: dict[str, Any], review: sqlite3.Row | dict[str, Any]
) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    automatic = isinstance(review, dict) and review.get("system_review") is True
    completeness = (
        review.get("completeness_status", "unknown") if isinstance(review, dict) else "unknown"
    )
    unchecked = automatic and completeness == "not_checked"
    title = "Unchecked extracted document" if unchecked else "Reviewed document"
    note = [
        "---",
        f'title: "{title}"',
        "source_kind: published_document",
        "source_version_id: " + metadata["source_version_id"],
        "source_asset_id: " + metadata["asset_id"],
        "source_sha256: " + metadata["source_sha256"],
        "review_id: " + review["id"],
        "knowledge_verified: false",
        "completeness_status: " + completeness,
        "---",
        "# " + title,
        "",
        "Saved by explicit policy; content completeness was not checked. "
        "Known gaps remain in the source record. No human review or independent verification."
        if unchecked
        else (
            "System rule-assessed extraction. Source/visual human review has not been performed. "
            "Independent verification has not been performed."
        )
        if automatic
        else (
            "Administrator-reviewed source extraction. "
            "Independent Gold review has not been performed."
        ),
        "",
        "Original: "
        + (
            f"/v1/vaults/{metadata['vault_id']}/versions/"
            f"{metadata['source_version_id']}/assets/{metadata['asset_id']}"
        ),
        "",
    ]
    if metadata.get("missing_references"):
        raise InvalidInput("markdown references require supplied mapping")
    markdown = metadata.get("source_format") == "markdown"
    if markdown:
        note = []
    units = candidate["pages"] if candidate["schema"] == 1 else candidate["segments"]
    source_units = []
    rendered_bytes = len("\n".join(note).encode())
    for unit in units:
        start = rendered_bytes + (1 if note else 0)
        offset = len(note)
        locator = (
            {"physical_page": unit["physical_page"], "pdf_page_label": unit["pdf_page_label"]}
            if candidate["schema"] == 1
            else unit["locator"]
        )
        if markdown:
            note.append(unit["text"])
        else:
            note.extend(["## " + canonical(locator), ""])
            fence = "`" * max(
                3, max((len(m.group()) + 1 for m in re.finditer("`+", unit["text"])), default=3)
            )
            note.extend([fence + "text", unit["text"], fence, ""])
        added = "\n".join(note[offset:]).encode()
        rendered_bytes = start + len(added)
        source_units.append(
            dict(
                unit_id=f"P{unit['physical_page']}" if candidate["schema"] == 1 else unit["id"],
                locator=locator,
                rendered_start=start,
                rendered_end=rendered_bytes,
            )
        )
    out = {}
    mapping = candidate.get("runtime", {}).get("mapping")
    if mapping:
        note.extend(["## 原件图片引用对应", ""])
        for reference in mapping["references"]:
            matches = [m for m in mapping["members"] if m["asset_id"] == reference["asset_id"]]
            if len(matches) == 1:
                member = matches[0]
                item = next(
                    (
                        a
                        for a in candidate.get("attachments", [])
                        if a["sha256"] == member["sha256"]
                    ),
                    None,
                )
                if item:
                    ext = "png" if item["media_type"] == "image/png" else "jpg"
                    name = "assets/" + item["sha256"] + "." + ext
                    note.extend([reference["literal"], "![](" + name + ")", ""])
    for item in candidate.get("attachments", []):
        ext = "png" if item["media_type"] == "image/png" else "jpg"
        name = "assets/" + item["sha256"] + "." + ext
        out[name] = base64.b64decode(item["data"], validate=True)
        note.extend(["![](" + name + ")", ""])
    out["note.md"] = "\n".join(note).encode()
    out["provenance.json"] = canonical(
        {
            "schema": "fiber-document-publication/v1",
            "run_id": metadata["run_id"],
            "source_version_id": metadata["source_version_id"],
            "asset_id": metadata["asset_id"],
            "source_sha256": metadata["source_sha256"],
            "result_sha256": metadata["result_sha256"],
            "review_id": review["id"],
            "review_actor": review["actor"] if automatic else "local_admin",
            "review_type": "system" if automatic else "human",
            "assessment": json.loads(review["assessment_json"]),
            "knowledge_verified": False,
            **(
                {"archive_mapping": candidate["runtime"]["mapping"]}
                if "mapping" in candidate.get("runtime", {})
                else {}
            ),
        }
    ).encode()
    return out, source_units
