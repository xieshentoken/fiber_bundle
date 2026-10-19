"""Connection-authorized note/module directory and bounded versioned evidence reads."""

import base64
import json
import sqlite3
from typing import Any, Literal

from pydantic import Field

from .. import auth, database, note_index, note_inventory, note_provenance, published_expert, vaults
from ..config import Config, InvalidInput
from ..inputs import canonical
from ..objects import digest
from ..payloads import Payload
from .knowledge_locators import page_ranges
from .visibility import scope, visible


class Read(Payload):
    module_id: str = Field(min_length=1, max_length=200)
    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["note", "module", "object", "anchor", "attachment", "expert_media"] = "note"
    offset: int = Field(default=0, strict=True, ge=0, le=2000)
    limit: int = Field(default=10, strict=True, ge=1, le=20)
    identity: str | None = Field(default=None, max_length=300)
    start_line: int = Field(default=1, strict=True, ge=1, le=1000000)
    end_line: int = Field(default=80, strict=True, ge=1, le=1000000)
    time_start_ms: int | None = Field(default=None, strict=True, ge=0, le=7200000)
    time_end_ms: int | None = Field(default=None, strict=True, ge=0, le=7200000)
    physical_page: int | None = Field(default=None, strict=True, ge=1, le=100000)


def note_id(vid: str, note: note_index.Note) -> str:
    return "note:" + digest((vid + ":" + (note.identity or note.relative_path)).encode())


def notes(
    config: Config, db: sqlite3.Connection, cid: str, vid: str, quality: str
) -> tuple[Any, Any, list[Any]]:
    vault = auth.grant(db, cid, vid, "query")
    inventory = note_inventory.read(config, db, vault)
    result = []
    for note in inventory.notes:
        provenance = note_provenance.describe(config, db, cid, vault, inventory, note)
        if visible(provenance, quality):
            result.append((note, provenance))
    return vault, inventory, result


def catalog(
    config: Config,
    db: sqlite3.Connection,
    cid: str,
    requested: list[str] | None,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    selected, quality = scope(config, db, cid, requested)
    if (
        type(offset) is not int
        or not 0 <= offset <= 100000
        or type(limit) is not int
        or not 1 <= limit <= 50
    ):
        raise InvalidInput("knowledge_catalog_limit")
    if selected is None:
        selected = [str(v["id"]) for v in vaults.client_vaults(db, cid)]
    items = []
    for vid in selected:
        _, _, entries = notes(config, db, cid, vid, quality)
        for note, provenance in entries:
            # Expert modules group their rendered objects; search retains the physical notes.
            if provenance.get("expert_projection") is True:
                continue
            items.append(
                {
                    "module_id": note_id(vid, note),
                    "version": note.sha256,
                    "vault_id": vid,
                    "title": note.title,
                    "kind": "note",
                    "note_id": note.identity,
                    "note_path": note.relative_path,
                    **provenance,
                }
            )
        from ..organization import packages

        items.extend(packages.listing(config, db, cid, vid))
        for row in db.execute(
            "SELECT p.task_id FROM source_expert_publications p JOIN source_imports i "
            "ON i.id=p.task_id LEFT JOIN publication_locations l ON l.kind='expert' "
            "AND l.publication_id=p.task_id WHERE COALESCE(l.vault_id,i.vault_id)=? "
            "AND p.state='published'",
            (vid,),
        ):
            try:
                module = published_expert.text(config, db, cid, row[0])
            except auth.Denied:
                continue
            items.append(
                {
                    **published_expert.public(module),
                    "kind": "expert",
                    "title": module["entry_note_path"].split("/")[-2],
                }
            )
    items.sort(key=lambda i: (i["vault_id"], i["module_id"]))
    page = items[offset : offset + limit]
    return {
        "schema": "fiber-knowledge-catalog/v1",
        "items": page,
        "next_offset": offset + limit if offset + limit < len(items) else None,
        "quality_scope": quality,
        "model_called": False,
    }


def read_evidence(
    config: Config, db: sqlite3.Connection, cid: str, request: Read
) -> dict[str, Any]:
    selected, quality = scope(config, db, cid, None)
    if request.module_id.startswith("expert:"):
        if request.kind == "expert_media":
            from ..expert_media import read

            return read(
                config, db, cid, request.module_id, request.expected_version, request.identity or ""
            )
        if (
            request.time_start_ms is not None
            or request.time_end_ms is not None
            or request.start_line != 1
            or request.end_line != 80
            or (request.physical_page is not None and request.kind not in {"object", "anchor"})
        ):
            raise InvalidInput("knowledge_evidence_locator")
        module = published_expert.text(
            config, db, cid, request.module_id.removeprefix("expert:"), request.expected_version
        )
        if request.kind not in {"module", "object", "anchor", "attachment"}:
            raise InvalidInput("knowledge_evidence_kind")
        if request.kind == "module":
            page = module["objects"][request.offset : request.offset + request.limit]
            return {
                **published_expert.public(module),
                "objects": [
                    {
                        "id": o["id"],
                        "title": o["title"],
                        "type": o["type"],
                        "evidence_ids": o["evidence_ids"],
                    }
                    for o in page
                ],
                "next_offset": request.offset + request.limit
                if request.offset + request.limit < len(module["objects"])
                else None,
                "model_called": False,
            }
        if request.kind == "attachment":
            auth.grant(db, cid, module["vault_id"], "original")
            name = request.identity or ""
            if name.endswith((".py", ".sh")):
                raise auth.Denied(404)
            raw = published_expert.member(
                config,
                db,
                cid,
                module["module_id"][7:],
                request.expected_version,
                name,
                original=True,
            )
            if len(raw) > 256 * 1024:
                raise InvalidInput("knowledge_evidence_capacity")
            data = {
                "name": name,
                "sha256": digest(raw),
                "data_base64": base64.b64encode(raw).decode(),
            }
        else:
            if request.kind == "object":
                index = json.loads(module["files"]["references/knowledge/index.json"])
                selected = next((o for o in index["objects"] if o["id"] == request.identity), None)
                if selected is None:
                    raise auth.Denied(404)
                group = [
                    json.loads(
                        published_expert.member(
                            config,
                            db,
                            cid,
                            module["module_id"][7:],
                            request.expected_version,
                            selected["path"],
                        )
                    )
                ]
            else:
                group = module["anchors"]
            found = [o for o in group if o["id"] == request.identity]
            if len(found) != 1:
                raise auth.Denied(404)
            data = found[0]
            if request.physical_page is not None and request.physical_page not in _pages(data):
                raise InvalidInput("knowledge_evidence_locator")
            if len(canonical(data).encode()) > 32768:
                raise InvalidInput("knowledge_evidence_capacity")
        return {
            **published_expert.public(module),
            "kind": request.kind,
            "evidence": data,
            "model_called": False,
        }
    if request.kind not in {"note", "attachment"}:
        raise InvalidInput("knowledge_evidence_kind")
    if selected is None:
        selected = [str(v["id"]) for v in vaults.client_vaults(db, cid)]
    for vid in selected:
        vault, inventory, entries = notes(config, db, cid, vid, quality)
        for note, provenance in entries:
            if note_id(vid, note) != request.module_id:
                continue
            if note.sha256 != request.expected_version:
                raise InvalidInput("knowledge_version_stale")
            current = note_index.read_note(
                inventory.root, note.relative_path, note_index.Limits().note_bytes
            )
            if current.sha256 != note.sha256:
                raise InvalidInput("knowledge_version_stale")
            if request.kind == "attachment":
                auth.grant(db, cid, vid, "original")
                original = next(
                    (
                        a
                        for a in provenance.get("originals", [])
                        if a["asset_id"] == request.identity
                    ),
                    None,
                )
                if original is None:
                    raise auth.Denied(404)
                from ..operations import original as read_original

                parts = original["url"].strip("/").split("/")
                raw = read_original(config, db, cid, parts[2], parts[4], parts[6], 256 * 1024)
                return {
                    "module_id": request.module_id,
                    "version": note.sha256,
                    "kind": "attachment",
                    "name": original["name"],
                    "sha256": digest(raw),
                    "data_base64": base64.b64encode(raw).decode(),
                    "model_called": False,
                }
            if (
                request.end_line < request.start_line
                or request.end_line - request.start_line >= 200
            ):
                raise InvalidInput("knowledge_evidence_locator")
            lines = current.text.splitlines()
            chosen = list(enumerate(lines, 1))[request.start_line - 1 : request.end_line]
            if request.physical_page is not None:
                ranges = page_ranges(current.text, provenance, request.physical_page)
                offsets, offset = {}, 0
                for n, line in enumerate(current.text.splitlines(keepends=True), 1):
                    offsets[n] = (offset, offset + len(line.encode()))
                    offset = offsets[n][1]
                chosen = [
                    (n, t)
                    for n, t in chosen
                    if any(offsets[n][0] < end and offsets[n][1] > start for start, end in ranges)
                ]
                if not chosen:
                    raise InvalidInput("knowledge_evidence_locator")
            if request.time_start_ms is not None or request.time_end_ms is not None:
                if (
                    request.time_start_ms is None
                    or request.time_end_ms is None
                    or request.time_end_ms <= request.time_start_ms
                ):
                    raise InvalidInput("knowledge_evidence_locator")
                import re

                chosen = [
                    (n, t)
                    for n, t in chosen
                    if (m := re.match(r"\[(\d+)–(\d+) ms\]", t))
                    and int(m[1]) < request.time_end_ms
                    and int(m[2]) > request.time_start_ms
                ]
            text = "\n".join(t for _, t in chosen)
            if not text or len(text) > 16000:
                raise InvalidInput("knowledge_evidence_capacity")
            return {
                "module_id": request.module_id,
                "version": note.sha256,
                "vault_id": vid,
                "note_path": note.relative_path,
                "note_id": note.identity,
                "lines": [n for n, _ in chosen],
                "text": text,
                **provenance,
                "model_called": False,
            }
    raise auth.Denied(404)


def _pages(value: Any) -> set[int]:
    result = set()
    if isinstance(value, dict):
        for key, v in value.items():
            if key in {"page", "physical_page"} and type(v) is int:
                result.add(v)
            elif key == "physical_pages" and isinstance(v, list) and len(v) == 2:
                if all(type(p) is int for p in v) and 1 <= v[0] <= v[1] <= 100000:
                    result.update(range(v[0], v[1] + 1))
            elif key == "pages" and isinstance(v, list):
                result.update(p for p in v if type(p) is int and 1 <= p <= 100000)
            else:
                result.update(_pages(v))
    elif isinstance(value, list):
        for item in value:
            result.update(_pages(item))
    return result


def query_expert(
    config: Config, cid: str, module_id: str, expected: str, question: str, limit: int = 5
) -> dict[str, Any]:
    if (
        not module_id.startswith("expert:")
        or not isinstance(question, str)
        or not question.strip()
        or not 1 <= len(question) <= 256
        or type(limit) is not int
        or not 1 <= limit <= 8
    ):
        raise InvalidInput("expert_query_scope")
    from ..expert_query import deliver, execute

    with database.locked(config), database.connect(config.state_root) as db:
        module = published_expert.text(config, db, cid, module_id[7:], expected)
    result = execute(module, question, limit)
    with database.locked(config), database.connect(config.state_root) as db:
        current = published_expert.text(config, db, cid, module_id[7:], expected)
        return deliver(module, current, result)
