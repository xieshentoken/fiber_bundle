"""Ordered document parts bound to the publication ledger, never note self-reports."""

import json
import sqlite3
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import note_index
from .article_capture import read_bounded
from .articles import ArticleError
from .config import IntegrityError, contained
from .document_split import MAX_PARTS, SplitRequired, split
from .inputs import canonical
from .objects import digest

MANIFEST = "publication-notes.json"
SCHEMA = "fiber-document-notes/v1"
MAX_MANIFEST_BYTES = 1024 * 1024


def identity(run_id: str, name: str) -> str:
    return "note:" + str(uuid5(NAMESPACE_URL, "fiber-document-part:" + run_id + ":" + name))


def publish_files(
    files: dict[str, bytes],
    metadata: dict[str, Any],
    review_id: str,
    units: list[dict[str, Any]],
    per_file: int,
    total: int,
) -> dict[str, bytes]:
    body = files["note.md"]
    parts = split(body, per_file, total)
    if len(parts) == 1:
        return files  # Original single-note manifests and bytes remain compatible.
    out = {name: data for name, data in files.items() if name != "note.md"}
    entries = []
    start = 0
    for index, data in enumerate(parts):
        name = "note.md" if index == 0 else f"part-{index + 1:04d}.md"
        end = start + len(data)
        out[name] = data
        entries.append(
            dict(
                path=name,
                note_id=identity(metadata["run_id"], name),
                sha256=digest(data),
                byte_count=len(data),
                rendered_start=start,
                rendered_end=end,
                source_units=[
                    u for u in units if u["rendered_start"] < end and u["rendered_end"] > start
                ],
            )
        )
        start = end
    catalog = (
        "# 文档分片目录\n\n"
        + "".join(f"[{i + 1}]({entry['path']})\n" for i, entry in enumerate(entries))
    ).encode()
    if len(catalog) > per_file:
        raise SplitRequired("document_split_unit_limit")
    if len(body) + len(catalog) > total:
        raise SplitRequired("document_split_total_limit")
    out["index.md"] = catalog
    navigation = dict(
        path="index.md",
        note_id=identity(metadata["run_id"], "index.md"),
        sha256=digest(catalog),
        byte_count=len(catalog),
        role="catalog",
    )
    out[MANIFEST] = canonical(
        dict(
            schema=SCHEMA,
            run_id=metadata["run_id"],
            review_id=review_id,
            source_version_id=metadata["source_version_id"],
            asset_id=metadata["asset_id"],
            source_sha256=metadata["source_sha256"],
            result_sha256=metadata["result_sha256"],
            rendered_sha256=digest(body),
            rendered_bytes=len(body),
            parts=entries,
            catalog=navigation,
        )
    ).encode()
    if len(out[MANIFEST]) > MAX_MANIFEST_BYTES:
        raise IntegrityError("document part manifest exceeds bound")
    return out


@dataclass(frozen=True)
class Part:
    note_id: str
    original_sha256: str
    version_id: str
    manifest: dict[str, Any]
    entry: dict[str, Any]
    role: str = "extraction"
    published_sha256: str | None = None


def bindings(
    db: sqlite3.Connection, vault_id: str, root: Path, relative_path: str | None = None
) -> dict[str, Part]:
    """Caller supplies the currently authorized root; check bound manifests, not chapter bodies.

    The publisher vault_key is historical write evidence. Restore explicitly rebinds
    current vault authorization while preserving that original ledger unchanged.
    """
    result: dict[str, Part] = {}
    manifest_bytes = 0
    bundle = relative_path.rsplit("/", 1)[0] if relative_path and "/" in relative_path else ""
    for row in db.execute(
        "SELECT p.*,r.version_id,l.package_root FROM document_publications p JOIN execution_runs r "
        "ON r.id=p.run_id LEFT JOIN publication_locations l "
        "ON l.kind='document' AND l.publication_id=p.run_id WHERE COALESCE"
        "(l.vault_id,r.vault_id)=? "
        "AND p.state='published' "
        "AND (? IS NULL OR COALESCE(l.package_root,p.relative_path)=?)",
        (vault_id, relative_path, bundle),
    ):
        ledger = json.loads(row["manifest_json"])
        expected = ledger.get(MANIFEST)
        if expected is None:
            continue
        package = row["package_root"] or row["relative_path"]
        from .publication_locations import get as location_get

        location = location_get(db, "document", row["run_id"])
        projections = location["plan"].get("body_projections", {}) if location else {}
        directory = contained(root, package)
        try:
            raw = read_bounded(directory, MANIFEST, MAX_MANIFEST_BYTES)
        except (OSError, ArticleError) as exc:
            raise IntegrityError("document part manifest unavailable") from exc
        manifest_bytes += len(raw)
        if manifest_bytes > 32 * 1024 * 1024 or len(result) > 10000:
            raise IntegrityError("document part inventory limit")
        if {"sha256": digest(raw), "byte_count": len(raw)} != expected:
            raise IntegrityError("document part manifest differs from ledger")
        manifest = json.loads(raw)
        if (
            manifest.get("schema") != SCHEMA
            or manifest.get("run_id") != row["run_id"]
            or manifest.get("review_id") != (row["system_review_id"] or row["review_id"])
            or manifest.get("source_version_id") != row["version_id"]
            or manifest.get("source_sha256") != row["source_sha256"]
            or manifest.get("result_sha256") != row["result_sha256"]
            or not isinstance(manifest.get("parts"), list)
            or not 2 <= len(manifest["parts"]) <= MAX_PARTS
        ):
            raise IntegrityError("document part source binding changed")
        offset = 0
        for index, entry in enumerate(manifest["parts"]):
            name = "note.md" if index == 0 else f"part-{index + 1:04d}.md"
            if (
                entry["path"] != name
                or entry["note_id"] != identity(row["run_id"], name)
                or entry["rendered_start"] != offset
                or entry["rendered_end"] != offset + entry["byte_count"]
                or ledger.get(name)
                != {"sha256": entry["sha256"], "byte_count": entry["byte_count"]}
            ):
                raise IntegrityError("document part order or content binding changed")
            offset = entry["rendered_end"]
            relative = package + "/" + name
            path = contained(root, relative)
            try:
                current = path.lstat()
            except FileNotFoundError:
                raise IntegrityError("document part missing; publication is incomplete") from None
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                raise IntegrityError("document part is not an independent regular file")
            if relative in result:
                raise IntegrityError("duplicate document part path")
            result[relative] = Part(
                entry["note_id"],
                entry["sha256"],
                row["version_id"],
                manifest,
                entry,
                published_sha256=projections.get(name, {}).get("published_sha256"),
            )
        catalog = manifest.get("catalog")
        if not isinstance(catalog, dict) or (
            catalog.get("path") != "index.md"
            or catalog.get("role") != "catalog"
            or catalog.get("note_id") != identity(row["run_id"], "index.md")
            or ledger.get("index.md")
            != {"sha256": catalog.get("sha256"), "byte_count": catalog.get("byte_count")}
        ):
            raise IntegrityError("document catalog binding changed")
        relative = package + "/index.md"
        try:
            info = contained(root, relative).lstat()
        except FileNotFoundError:
            raise IntegrityError("document catalog missing; publication is incomplete") from None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise IntegrityError("document catalog is not an independent regular file")
        result[relative] = Part(
            catalog["note_id"],
            catalog["sha256"],
            row["version_id"],
            manifest,
            catalog,
            "catalog",
            published_sha256=projections.get("index.md", {}).get("published_sha256"),
        )
        if offset != manifest["rendered_bytes"]:
            raise IntegrityError("document parts do not cover rendered document")
    return result


def annotate(note: note_index.Note, parts: dict[str, Part]) -> note_index.Note:
    bound = parts.get(note.relative_path)
    if bound is None:
        return note
    if note.identity is not None and note.identity != bound.note_id:
        raise IntegrityError("registered identity conflicts with document part")
    return replace(note, identity=bound.note_id)
