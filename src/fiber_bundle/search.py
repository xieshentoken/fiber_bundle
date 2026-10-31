"""Deterministic, current-file evidence search scoped before any content read."""

import sqlite3
from dataclasses import replace
from typing import Any

from . import auth, note_index, note_publications, publication_locations, vaults
from .config import Config, IntegrityError, InvalidInput, StorageError
from .search_matching import best_passage, terms
from .search_snapshot import SearchSnapshot, combine


def scope(db: sqlite3.Connection, client_id: str, requested: list[str] | None) -> list[sqlite3.Row]:
    active = db.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
    if active is None or not active[0]:
        raise auth.Denied(401)
    if requested is None:
        requested = [
            row[0]
            for row in db.execute(
                "SELECT v.id FROM vaults v JOIN client_vault_grants g ON g.vault_id=v.id "
                "WHERE g.client_id=? AND g.can_query=1 AND v.default_query=1 AND v.status='active' "
                "AND v.attachment_state='attached' "
                "ORDER BY v.id",
                (client_id,),
            )
        ]
    if len(requested) > 20 or len(set(requested)) != len(requested):
        raise InvalidInput("invalid search vault list")
    # Validate every explicit target first; never return partial results for a denied scope.
    return [auth.grant(db, client_id, vid, "query") for vid in requested]


def vault_search(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    query: str,
    requested: list[str] | None = None,
    limit: int = 10,
    *,
    snapshot: SearchSnapshot | None = None,
    combine_sources: bool = True,
) -> dict[str, Any]:
    """Caller holds the shared storage lock through authentication and response delivery."""
    patterns = terms(query)
    if type(limit) is not int or not 1 <= limit <= 20:
        raise InvalidInput("invalid search result limit")
    from .agent_connections.visibility import scope as connection_scope
    from .agent_connections.visibility import visible

    requested, quality = connection_scope(config, db, client_id, requested)
    selected = scope(db, client_id, requested)
    if snapshot is None:
        with SearchSnapshot(config, db, client_id) as request_snapshot:
            return vault_search(
                config,
                db,
                client_id,
                query,
                requested,
                limit,
                snapshot=request_snapshot,
                combine_sources=combine_sources,
            )
    if snapshot.config is not config or snapshot.db is not db or snapshot.client_id != client_id:
        raise InvalidInput("search snapshot scope mismatch")
    candidates: list[tuple[int, dict[str, Any], note_index.Note]] = []
    roots = {}
    bindings = {}
    managed_entries = {}
    indexed: set[str] = set()
    for vault in selected:
        from . import search_provenance

        inventory, matches, cached = snapshot.candidates(vault, query)
        if cached:
            indexed.add(vault["id"])
        roots[vault["id"]] = inventory.root
        bindings[vault["id"]] = inventory.bindings
        managed_entries[vault["id"]] = publication_locations.inventory(db, vault["id"])[0]
        intake_locations = search_provenance.intake_locations(db, vault["id"])
        for best, note in matches:
            from . import note_provenance

            try:
                provenance = note_provenance.describe(
                    config,
                    db,
                    client_id,
                    vault,
                    inventory,
                    note,
                    intake_locations=intake_locations,
                )
            except auth.Denied:
                continue
            if not visible(provenance, quality):
                continue
            item = {
                "vault_id": vault["id"],
                "note_path": note.relative_path,
                "title": note.title,
                "sha256": note.sha256,
                "line": best[1],
                "snippet": best[2],
                "line_end": best[1] + len(best[2].splitlines()) - 1,
                **provenance,
            }
            if note.identity is not None:
                item["note_id"] = note.identity
            candidates.append((best[0], item, note))
    candidates.sort(key=lambda item: (-item[0], item[1]["vault_id"], item[1]["note_path"]))
    results: list[dict[str, Any]] = []
    delivery_registrations = {}
    seen_experts = set()
    deduplicated = []
    for candidate in candidates:
        item = candidate[1]
        if item.get("expert_projection"):
            key = (item["expert_module_id"], item["expert_module_version"])
            if key in seen_experts:
                continue
            seen_experts.add(key)
        deduplicated.append(candidate)
    for score, item, note in deduplicated[:limit]:
        vaults.checked_vault(config, auth.grant(db, client_id, item["vault_id"], "query"))
        from .document_creation_save import search_source

        search_source(db, client_id, item["vault_id"], note.relative_path, note.sha256)
        try:
            current = snapshot.read_note(
                item["vault_id"], roots[item["vault_id"]], note.relative_path
            )
        except (StorageError, OSError):
            if item["vault_id"] not in snapshot.managed:
                raise
            snapshot.incomplete.add(item["vault_id"])
            continue
        if item["vault_id"] in snapshot.managed and (
            current.sha256 != note.sha256 or current.file_key != note.file_key
        ):
            snapshot.incomplete.add(item["vault_id"])
            snapshot.reads.pop((item["vault_id"], note.relative_path), None)
            continue
        managed = managed_entries[item["vault_id"]].get(note.relative_path)
        current = publication_locations.annotate(current, managed)
        if managed and (current.identity, current.publication_id) != (
            note.identity,
            note.publication_id,
        ):
            raise IntegrityError("search index differs from committed layout")
        if current.sha256 != note.sha256:
            raise IntegrityError("search evidence changed before delivery")
        if current.title != note.title:
            raise IntegrityError("search title changed before delivery")
        if not note.text and best_passage(current.title, current.text, patterns) != (
            score,
            item["line"],
            item["snippet"],
        ):
            raise IntegrityError("search index passage differs from current evidence")
        if note.identity is not None and current.file_key != note.file_key:
            raise IntegrityError("search note identity changed before delivery")
        if item["vault_id"] in indexed:
            from . import document_parts, note_identities, note_identity_store

            if item["vault_id"] not in delivery_registrations:
                delivery_registrations[item["vault_id"]] = note_identity_store.records(
                    config, item["vault_id"]
                )
            info = roots[item["vault_id"]].stat()
            resolved = note_identities.resolve(
                [
                    document_parts.annotate(
                        replace(current, publication_id=note.publication_id),
                        document_parts.bindings(
                            db, item["vault_id"], roots[item["vault_id"]], note.relative_path
                        ),
                    )
                ],
                delivery_registrations[item["vault_id"]],
                (info.st_dev, info.st_ino),
            )[0]
            if resolved.identity != note.identity:
                raise IntegrityError("search index identity differs from current registration")
        if note.publication_id is not None and managed is None:
            if note.relative_path.rsplit("/", 1)[0] != note.publication_path:
                raise IntegrityError("search publication path differs from current evidence")
            data = note_publications.read_marker(
                roots[item["vault_id"]],
                note.publication_path or note.relative_path.removesuffix("/note.md"),
                note_index.Limits().note_bytes,
            )
            bound = note_publications.identify(data, bindings[item["vault_id"]])
            if bound is None or bound.publication_id != note.publication_id:
                raise IntegrityError("search publication identity changed before delivery")
        item["citation"] = f"[{len(results) + 1}]"
        results.append(item)
    from .organization import packages

    external = []
    for vault in selected:
        external.extend(packages.search(config, db, client_id, vault["id"], query))
    if external:
        results = (
            combine(external, results, limit) if combine_sources else (external + results)[:limit]
        )
        for ordinal, item in enumerate(results, 1):
            item["citation"] = f"[{ordinal}]"
    coverage = {
        v["id"]: value for v in selected if (value := snapshot.coverage(v["id"])) is not None
    }
    incomplete = any(not value["complete"] for value in coverage.values())
    if incomplete and not results:
        from .note_search_cache import IndexUnavailable

        raise IndexUnavailable("note_index_update_required")
    return {
        "status": "evidence_found" if results else "no_evidence",
        "results": results,
        "searched_vault_ids": [v["id"] for v in selected],
        "scope": "vault_notes",
        **({"coverage": coverage, "coverage_complete": not incomplete} if coverage else {}),
        "index_mode": (
            "persistent_chunks"
            if len(indexed) == len(selected) and indexed
            else "mixed_literal_sources"
            if indexed
            else "current_read_only_snapshot"
        ),
    }


def search(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    query: str,
    requested: list[str] | None = None,
    limit: int = 10,
    *,
    session_id: str | None = None,
    snapshot: SearchSnapshot | None = None,
    combine_sources: bool = True,
) -> dict[str, Any]:
    """Combine current-session evidence and default/explicit Vault notes without widening scope."""
    from .session_search import session_search

    if session_id is None:
        return vault_search(
            config,
            db,
            client_id,
            query,
            requested,
            limit,
            snapshot=snapshot,
            combine_sources=combine_sources,
        )
    scope(db, client_id, requested)
    terms(query)
    if type(limit) is not int or not 1 <= limit <= 20:
        raise InvalidInput("invalid search result limit")
    current = session_search(config, db, client_id, session_id, query, requested, limit)
    result = vault_search(
        config,
        db,
        client_id,
        query,
        requested,
        limit,
        snapshot=snapshot,
        combine_sources=combine_sources,
    )
    result["results"] = (current + result["results"])[:limit]
    for index, item in enumerate(result["results"], 1):
        item["citation"] = f"[{index}]"
    result["status"] = "evidence_found" if result["results"] else "no_evidence"
    result["scope"] = "vault_notes_and_session"
    result["searched_session_id"] = session_id
    return result
