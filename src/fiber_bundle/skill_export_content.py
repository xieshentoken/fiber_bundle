"""Portable content projection; references stay local and child expert bytes stay intact."""

import base64
import posixpath
import re
import sqlite3
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from . import auth, note_index, published_expert
from .agent_connections import knowledge
from .agent_connections.visibility import scope
from .config import Config, InvalidInput
from .inputs import canonical
from .note_transfer_links import mask
from .objects import digest
from .publication_file_io import read
from .vault_scope import for_vault

MAX_BYTES = 64 * 1024 * 1024
LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(([^\s)]+)\)")
WIKI = re.compile(r"(!?)\[\[([^\]\n]+)\]\]")


def safe(files: dict[str, bytes]) -> None:
    for name, raw in files.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise InvalidInput("skill_export_path_invalid")
        if any(p in {".local-state", ".git", ".env"} for p in path.parts):
            raise InvalidInput("skill_export_sensitive_content")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if re.search(
            r"(?:/Users/|/home/|/private/|/tmp/|file://|[A-Z]:\\\\|Bearer\s+[\w-]{16,}|"
            r"sk-[A-Za-z0-9_-]{16,}|(?:api[_-]?key|app[_-]?secret|password|access[_-]?token)"
            r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_+/=-]{12,})",
            text,
            re.I,
        ):
            raise InvalidInput("skill_export_sensitive_content")
    if sum(len(raw) for raw in files.values()) > MAX_BYTES:
        raise InvalidInput("skill_export_capacity")


def current(
    config: Config,
    db: sqlite3.Connection,
    cid: str,
    member: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> Any:
    if member["kind"] == "expert":
        return published_expert.load(config, db, cid, member["module_id"][7:], member["version"])
    _, quality = scope(config, db, cid, [member["vault_id"]])
    cache = cache if cache is not None else {}
    key = member["vault_id"] + ":" + quality
    if key not in cache:
        cache[key] = knowledge.notes(config, db, cid, member["vault_id"], quality)
    vault, inventory, entries = cache[key]
    for note, provenance in entries:
        if knowledge.note_id(member["vault_id"], note) == member["module_id"]:
            latest = note_index.read_note(
                inventory.root, note.relative_path, note_index.Limits().note_bytes
            )
            if latest.sha256 != member["version"]:
                raise InvalidInput("skill_export_source_changed")
            return vault, inventory, latest, provenance
    raise auth.Denied(404)


def project(
    config: Config,
    db: sqlite3.Connection,
    cid: str,
    member: dict[str, Any],
    members: list[dict[str, Any]],
    originals: bool,
    cache: dict[str, Any] | None = None,
) -> tuple[dict[str, bytes], list[str]]:
    source = current(config, db, cid, member, cache)
    key = digest(member["module_id"].encode())[:24]
    folder = "modules/" + key
    gaps: list[str] = []
    if member["kind"] == "expert":
        files = {folder + "/" + name: raw for name, raw in source["files"].items()}
        if not originals and any(
            PurePosixPath(n).suffix.lower()
            in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".wav", ".mp3"}
            for n in files
        ):
            raise InvalidInput("skill_export_child_originals_required")
        safe(files)  # Reject the whole child rather than silently invalidate its manifest.
        return files, ["专家 Draft 未经独立审核；完整核验可能需要未包含的外部原件。"]
    vault, inventory, note, provenance = source
    files = {}
    by_path = {i.get("note_path"): i for i in members if i["vault_id"] == member["vault_id"]}

    def link(image: str, label: str, target: str) -> str:
        parts = urlsplit(target)
        if parts.scheme in {"http", "https"}:
            if parts.username or parts.password:
                raise InvalidInput("skill_export_sensitive_content")
            return f"{image}[{label}]({target})"
        if parts.scheme or target.startswith(("/", "\\")):
            raise InvalidInput("skill_export_path_invalid")
        relative = posixpath.normpath(
            posixpath.join(posixpath.dirname(note.relative_path), unquote(parts.path))
        )
        linked = by_path.get(relative) or by_path.get(relative + ".md")
        if linked:
            other = digest(linked["module_id"].encode())[:24]
            entry = "SKILL.md" if linked["kind"] == "expert" else "note.md"
            return f"{image}[{label}](../{other}/{entry})"
        if originals and parts.path and for_vault(inventory.root, vault).allows(relative):
            auth.grant(db, cid, member["vault_id"], "original")
            try:
                data, _ = read(inventory.root, relative)
            except (OSError, InvalidInput):
                data = None
            if data is not None:
                name = "assets/" + digest(data) + PurePosixPath(relative).suffix.lower()
                files[folder + "/" + name] = data
                return f"{image}[{label}]({name})"
        gaps.append("未包含引用：" + label[:120])
        return label + "（引用未包含）"

    # Existing Markdown masking keeps examples in fences/inline code byte-for-byte.
    visible = mask(note.text)
    edits = []
    for pattern, wiki in ((LINK, False), (WIKI, True)):
        for match in pattern.finditer(visible):
            original = pattern.fullmatch(note.text[match.start() : match.end()])
            if original is None:
                continue
            replacement = (
                link(original[1], original[2].split("|")[-1], original[2].split("|")[0])
                if wiki
                else link(original[1], original[2], original[3])
            )
            edits.append((match.start(), match.end(), replacement))
    text = note.text
    for start, stop, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[stop:]
    files[folder + "/note.md"] = text.encode()
    files[folder + "/source.json"] = canonical(
        {
            "module_id": member["module_id"],
            "version": member["version"],
            "source_version_id": provenance.get("source_version_id"),
            "completeness_status": provenance.get("completeness_status", "unknown"),
            "known_gaps": provenance.get("known_gaps", []),
            "source_kind": provenance.get("source_kind", "unknown"),
            "independent_evidence": provenance.get("independent_evidence", False),
            "review_state": provenance.get("review_state", "unknown"),
            "document_source_spans": provenance.get("document_source_spans"),
            "document_part": provenance.get("document_part"),
        }
    ).encode()
    if originals:
        auth.grant(db, cid, member["vault_id"], "original")
        for asset in provenance.get("originals", []):
            try:
                result = knowledge.read_evidence(
                    config,
                    db,
                    cid,
                    knowledge.Read(
                        module_id=member["module_id"],
                        expected_version=member["version"],
                        kind="attachment",
                        identity=asset["asset_id"],
                    ),
                )
                files[folder + "/originals/" + digest(asset["asset_id"].encode())] = (
                    base64.b64decode(result["data_base64"])
                )
            except InvalidInput:
                gaps.append("原件未包含（超过有界读取容量）：" + asset["name"][:120])
    else:
        gaps.append("未包含原件、图片或音视频附件；核验这些证据需要原来源。")
    safe(files)
    return files, gaps
