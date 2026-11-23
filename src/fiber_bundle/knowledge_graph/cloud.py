"""Bounded v2 projection and search over one authorized entity/evidence table."""

import sqlite3
from typing import Any

from .. import auth, note_search_cache
from ..config import Config, InvalidInput
from . import (
    cloud_entities,
    cloud_ledger,
    cloud_links,
    cloud_memberships,
    cloud_missing_publications,
    cloud_sources,
)
from .contracts import fingerprint

MAX_NODES = 10_000
MAX_EDGES = 40_000


def _verify(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    source: cloud_sources.Snapshot,
    table: cloud_entities.Table,
) -> None:
    cloud_sources.verify(config, db, client, vault, source)
    if table.ledger is not None and cloud_ledger.read(config, vault).token != table.ledger.token:
        raise note_search_cache.IndexUnavailable("note_index_update_required")
    if table.missing_proof is not None and table.ledger is not None:
        current = cloud_missing_publications.read(
            config,
            db,
            client,
            vault,
            source.root,
            table.ledger.registrations,
            table.missing_proof.targets,
        )
        if current.token != table.missing_proof.token:
            raise note_search_cache.IndexUnavailable("note_index_update_required")


def read(
    config: Config,
    main: sqlite3.Connection,
    client: str,
    vault: str,
    *,
    max_nodes: int = 5000,
    focus: str | None = None,
) -> dict[str, Any]:
    if type(max_nodes) is not int or not 1 <= max_nodes <= MAX_NODES:
        raise InvalidInput("note_cloud_node_limit")
    source = cloud_sources.read(config, main, client, vault)
    table = cloud_entities.build(config, main, client, vault, source)
    if focus is not None and focus not in table.nodes:
        focus = next((key for key, node in table.nodes.items() if focus in node["aliases"]), None)
        if focus is None:
            raise auth.Denied(404)
    observed: dict[str, cloud_memberships.Evidence] = {}
    _, relations, links = cloud_links.read(
        config,
        main,
        vault,
        source,
        path_ids=table.paths,
        moc_ids={path: mid for mid, path in table.moc_paths.items()},
        observed=observed,
    )
    explicit = [
        {"id": fingerprint([a, b, kind]), "source": a, "target": b, "kind": kind}
        for a, b, kind in relations
    ]
    memberships, membership_stats = cloud_memberships.edges(source, table, observed, vault=vault)
    # Preserve saved ownership at a display-edge boundary before ordinary links.
    all_edges = memberships + explicit
    priority = []
    if focus:
        priority = [focus] + sorted(
            {
                edge["target"] if edge["source"] == focus else edge["source"]
                for edge in all_edges
                if focus in (edge["source"], edge["target"])
            }
        )
    selected = set((priority + sorted(table.nodes.keys() - set(priority)))[:max_nodes])
    edges = [
        edge for edge in all_edges if edge["source"] in selected and edge["target"] in selected
    ][:MAX_EDGES]
    nodes = [table.nodes[key] for key in sorted(selected)]
    omitted_nodes, omitted_edges = len(table.nodes) - len(nodes), len(all_edges) - len(edges)
    _verify(config, main, client, vault, source, table)
    return {
        "schema": "fiber-note-cloud/v2",
        "client_id": client,
        "vault_id": vault,
        "snapshot_token": table.token,
        "nodes": nodes,
        "edges": edges,
        "focus": focus,
        "coverage": {
            **source.coverage,
            **links,
            **cloud_entities.statistics(table),
            **membership_stats,
            "complete": bool(
                source.coverage["complete"]
                and links["links_complete"]
                and table.classification_complete
                and not table.omitted_memberships
                and not omitted_nodes
                and not omitted_edges
            ),
            "displayed_notes": sum(node["kind"] in {"note", "moc"} for node in nodes),
            "displayed_nodes": len(nodes),
            "displayed_edges": len(edges),
            "omitted_nodes": omitted_nodes,
            "omitted_edges": omitted_edges,
            "node_limit": max_nodes,
            "edge_limit": MAX_EDGES,
        },
    }


def search(
    config: Config,
    main: sqlite3.Connection,
    client: str,
    vault: str,
    query: str,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    if not 1 <= len(query) <= 200 or type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidInput("note_cloud_search_invalid")
    source = cloud_sources.read(config, main, client, vault)
    table = cloud_entities.build(config, main, client, vault, source)
    words = query.casefold().split()
    matched = [
        node
        for node in table.nodes.values()
        if words and all(word in (node["label"] + " " + node["path"]).casefold() for word in words)
    ]
    _verify(config, main, client, vault, source, table)
    return {
        "schema": "fiber-note-cloud/v2",
        "client_id": client,
        "vault_id": vault,
        "snapshot_token": table.token,
        "items": matched[:limit],
        "matched_count": len(matched),
        "truncated": len(matched) > limit,
        "coverage": {
            **source.coverage,
            **cloud_entities.statistics(table),
            "membership_projection_complete": table.omitted_memberships == 0,
            "moc_membership_unavailable": table.omitted_memberships,
        },
    }
