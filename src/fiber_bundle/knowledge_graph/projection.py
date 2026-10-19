"""Bounded cross-batch note graph; current source checks precede semantic edges and details."""

import posixpath
import sqlite3
from dataclasses import asdict
from typing import Any

from .. import (
    auth,
    note_index,
    note_inventory,
    note_provenance,
    note_search_cache,
    note_search_inventory,
    source_import_markdown,
    vaults,
)
from ..config import Config, InvalidInput
from ..inputs import canonical
from . import catalog, intake, jobs, policy, store
from .contracts import fingerprint

MAX_BATCHES = 32
MAX_NODES = 1000
MAX_EDGES = 5000
MAX_SOURCE_BYTES = 32 * 1024 * 1024


def inventory(
    config: Config, main: sqlite3.Connection, cid: str, vid: str, selected: set[str]
) -> note_inventory.Inventory:
    vault = auth.grant(main, cid, vid, "query")
    if note_search_cache.status(config, main, vault)["state"] != "ready":
        raise note_search_cache.IndexUnavailable("note_index_update_required")
    return note_search_inventory.read(config, main, vault, selected=selected)


def resolve(note: note_index.Note, reference: str, paths: dict[str, str]) -> str | None:
    target = reference.split("#", 1)[0].split("|", 1)[0]
    if not target or target.startswith(("/", "//")) or ":" in target or "\\" in target:
        return None
    adjacent = posixpath.normpath(posixpath.join(posixpath.dirname(note.relative_path), target))
    candidates = {target, adjacent}
    if not posixpath.splitext(target)[1]:
        candidates |= {p + ".md" for p in candidates}
    found = {paths[p] for p in candidates if p in paths}
    if not found and "/" not in target:
        found = {
            identity
            for path, identity in paths.items()
            if posixpath.basename(path) in {target, target + ".md"}
        }
    return next(iter(found)) if len(found) == 1 else None


def read(
    config: Config, main: sqlite3.Connection, cid: str, vid: str, focus: str | None = None
) -> dict[str, Any]:
    jobs.authorize(config, cid, vid)
    base = inventory(config, main, cid, vid, set())
    registered = {n.identity: n for n in base.notes if n.identity}
    paths = {n.relative_path: n.identity for n in base.notes if n.identity}
    if focus and focus not in registered:
        raise auth.Denied(404)
    profile = policy.current(config)
    selected = set(sorted(registered)[:MAX_NODES])
    if focus:
        selected = {focus}
        initial = inventory(config, main, cid, vid, selected)
        note = next(n for n in initial.notes if n.identity == focus)
        selected.update(
            value
            for ref in source_import_markdown.references(note.text)
            if (value := resolve(note, ref, paths))
        )
        with store.connection(config) as side:
            if side:
                for row in side.execute(
                    "SELECT prepared_json,result_json,profile_json FROM jobs "
                    "WHERE client_id=? AND vault_id=? "
                    "AND state='completed' ORDER BY created_at DESC LIMIT ?",
                    (cid, vid, MAX_BATCHES),
                ):
                    import json

                    if (
                        profile is None
                        or not profile.enabled
                        or policy.parse(json.loads(row[2])) != profile
                    ):
                        continue
                    prepared = store.decode(row[0])
                    if prepared.permission_epoch != intake.epoch(main, cid, vid):
                        continue
                    from .results import decode_result

                    result = decode_result(row[1].encode(), prepared.request)
                    for moc in result.mocs:
                        if focus in moc.note_ids:
                            selected.update(moc.note_ids)
                    for relation in result.relations:
                        support = {span.note_id for span in relation.evidence}
                        if focus in support:
                            selected.update(support)
        selected &= set(registered)
        selected = {focus} | set(sorted(selected - {focus})[: MAX_NODES - 1])
    # Enforce the aggregate byte boundary before opening any selected source body.
    root = base.root
    if (
        sum((root / registered[i].relative_path).stat().st_size for i in selected)
        > MAX_SOURCE_BYTES
    ):
        raise InvalidInput("graph_display_source_byte_limit")
    current = inventory(config, main, cid, vid, selected)
    if sum(len(n.text.encode()) for n in current.notes) > MAX_SOURCE_BYTES:
        raise InvalidInput("graph_display_source_byte_limit")
    fresh = {
        n.identity: n for n in current.notes if n.identity is not None and n.identity in selected
    }
    vault = auth.grant(main, cid, vid, "query")
    nodes = [
        {
            "id": identity,
            "kind": "note",
            "label": note.title,
            "path": note.relative_path,
            "sha256": note.sha256,
        }
        for identity, note in sorted(fresh.items())
    ]
    edges: dict[str, dict[str, Any]] = {}
    groups: dict[str, dict[str, Any]] = {}
    omitted_groups: set[str] = set()
    candidates: dict[str, dict[str, Any]] = {}
    coverage: set[str] = set()
    archived: set[str] = set()
    omitted_edges = 0

    def add(source: str, target: str, kind: str, candidate: str | None = None) -> None:
        nonlocal omitted_edges
        if (
            source == target
            or source not in fresh
            and source not in groups
            or target not in fresh
            and target not in groups
        ):
            return
        key = fingerprint([min(source, target), max(source, target), kind])
        if key not in edges:
            if len(edges) >= MAX_EDGES:
                omitted_edges += 1
                return
            edges[key] = {
                "id": key,
                "source": source,
                "target": target,
                "kind": kind,
                "candidates": [],
            }
        if candidate and candidate not in edges[key]["candidates"]:
            edges[key]["candidates"].append(candidate)

    for identity, note in fresh.items():
        for reference in source_import_markdown.references(note.text):
            if target := resolve(note, reference, paths):
                add(identity, target, "explicit")
    batch_count = 0
    with store.connection(config) as side:
        if side:
            batch_count = side.execute(
                "SELECT count(*) FROM jobs WHERE client_id=? AND vault_id=? AND state='completed'",
                (cid, vid),
            ).fetchone()[0]
            if profile and profile.enabled:
                archived.update(
                    row[0]
                    for row in side.execute(
                        "SELECT DISTINCT json_extract(n.value,'$.note_id') "
                        "FROM jobs j, json_each(j.prepared_json,'$.request.notes') n "
                        "WHERE j.client_id=? AND j.vault_id=? AND j.state='completed' "
                        "AND j.profile_json=? "
                        "AND json_extract(j.prepared_json,'$.permission_epoch')=?",
                        (cid, vid, canonical(asdict(profile)), intake.epoch(main, cid, vid)),
                    )
                    if row[0] in registered
                )
            for row in side.execute(
                "SELECT id FROM jobs WHERE client_id=? AND vault_id=? "
                "AND state='completed' ORDER BY created_at DESC LIMIT ?",
                (cid, vid, MAX_BATCHES),
            ).fetchall():
                generation = catalog.generation(
                    config, cid, vid, job_id=row[0], inventory_snapshot=current
                )
                if generation is None:
                    continue
                archived.update(s.note_id for s in generation.prepared.request.notes)
                coverage.update(generation.visible)
                entities = {e.id: e.evidence.note_id for e in generation.result.entities}
                for item in catalog.objects(generation):
                    if not item["support"] <= generation.visible or item["type"] == "entity":
                        continue
                    review = catalog.decision(side, cid, vid, generation.profile.digest, item["id"])
                    if review["state"] == "rejected":
                        continue
                    key = generation.job_id + ":" + item["id"]
                    kind = "accepted" if review["state"] == "accepted" else "suggested"
                    candidates[key] = {
                        "id": item["id"],
                        "job_id": generation.job_id,
                        "type": item["type"],
                        "configuration": generation.profile.digest,
                        "review": review,
                        "support": sorted(item["support"]),
                    }
                    if item["type"] == "moc":
                        group = "group:" + fingerprint(item["value"]["title"])
                        if group not in groups and len(nodes) + len(groups) < MAX_NODES:
                            groups[group] = {
                                "id": group,
                                "kind": "group",
                                "label": item["value"]["title"],
                            }
                        if group not in groups:
                            omitted_groups.add(group)
                        for identity in item["support"]:
                            add(group, identity, kind, key)
                    else:
                        relation = item["value"]
                        source, target = (
                            entities.get(relation["source"]),
                            entities.get(relation["target"]),
                        )
                        if source and target:
                            add(source, target, kind, key)
    nodes.extend(groups.values())
    # Recheck scope and index generation at response publication, after every batch.
    auth.grant(main, cid, vid, "query")
    if note_search_cache.status(config, main, vault)["state"] != "ready":
        raise note_search_cache.IndexUnavailable("note_index_update_required")
    return {
        "state": "ready",
        "scope": {"client_id": cid, "vault_id": vid},
        "configuration": profile.digest if profile else None,
        "snapshot": fingerprint(
            [
                intake.epoch(main, cid, vid),
                [(n["id"], n.get("sha256")) for n in nodes],
                sorted(edges),
            ]
        ),
        "nodes": nodes,
        "edges": list(edges.values()),
        "candidates": candidates,
        "statistics": {
            "registered_notes": len(registered),
            "analyzed_current_visible": len(coverage),
            "analyzed_recorded": len(archived),
            "displayed_notes": len(fresh),
            "displayed_groups": len(groups),
            "displayed_nodes": len(nodes),
            "displayed_edges": len(edges),
            "explicit_links": sum(e["kind"] == "explicit" for e in edges.values()),
            "semantic_links": sum(e["kind"] != "explicit" for e in edges.values()),
            "omitted_edges": omitted_edges,
            "omitted_groups": len(omitted_groups),
            "partial": len(fresh) < len(registered)
            or omitted_edges > 0
            or bool(omitted_groups)
            or batch_count > MAX_BATCHES,
            "displayed_batch_limit": MAX_BATCHES,
            "completed_batches": batch_count,
            "view": "neighbors" if focus else "bounded_vault",
            "node_limit": MAX_NODES,
            "edge_limit": MAX_EDGES,
        },
    }


def search(
    config: Config, main: sqlite3.Connection, cid: str, vid: str, value: str
) -> list[dict[str, str]]:
    if not 1 <= len(value) <= 100:
        raise InvalidInput("graph_search_invalid")
    current = inventory(config, main, cid, vid, set())
    return [
        {"id": n.identity, "label": n.title, "path": n.relative_path}
        for n in current.notes
        if n.identity and value.casefold() in (n.title + " " + n.relative_path).casefold()
    ][:50]


def detail(
    config: Config, main: sqlite3.Connection, cid: str, vid: str, identity: str
) -> dict[str, Any]:
    current = inventory(config, main, cid, vid, {identity})
    note = next((n for n in current.notes if n.identity == identity), None)
    if note is None:
        raise auth.Denied(404)
    vault = auth.grant(main, cid, vid, "query")
    provenance = note_provenance.describe(config, main, cid, vault, current, note)
    return {
        "id": identity,
        "title": note.title,
        "path": note.relative_path,
        "sha256": note.sha256,
        "excerpt": note.text[:800],
        "source_kind": provenance["source_kind"],
        "absolute_path": str(vaults.checked_vault(config, vault) / note.relative_path),
    }
