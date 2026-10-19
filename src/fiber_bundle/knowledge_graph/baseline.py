"""Measure the existing retrieval path on the explicit frozen corpus, without invoking a tool."""

from typing import Any

from .. import auth, database, search
from ..config import Config, IntegrityError, InvalidInput
from .contracts import fingerprint
from .intake import Prepared, revalidate


def run(
    config: Config,
    authorization: str,
    prepared: Prepared,
    queries: tuple[str, ...],
    *,
    enabled: bool = False,
) -> dict[str, Any]:
    if not 1 <= len(queries) <= 20 or len(set(queries)) != len(queries):
        raise InvalidInput("graph_baseline_query_limit")
    revalidate(config, authorization, prepared, enabled=enabled)
    selected = {note.note_id: note for note in prepared.request.notes}
    results = []
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            client_id = auth.client(db, authorization)["id"]
            if client_id != prepared.request.client_id:
                raise auth.Denied()
            for query in queries:
                result = search.search(
                    config, db, client_id, query, [prepared.request.vault_id], 10
                )
                for hit in result["results"]:
                    note = selected.get(hit.get("note_id"))
                    if note is None or (
                        hit["sha256"],
                        hit["source_kind"],
                        hit["source_version_id"],
                    ) != (
                        note.sha256,
                        note.source_kind,
                        note.source_version_id,
                    ):
                        # Do not silently filter/re-rank the existing baseline into a stronger one.
                        raise IntegrityError("graph_baseline_contains_unselected_or_changed_source")
                results.append({"query": query, **result})
        finally:
            db.close()
    revalidate(config, authorization, prepared, enabled=enabled)
    return {
        "schema": "fiber-knowledge-graph-baseline/v1",
        "input_sha256": prepared.request.input_sha256,
        "configuration_sha256": prepared.request.configuration_sha256,
        "query_set_sha256": fingerprint(queries),
        "method": "existing_search.search",
        "top_k": 10,
        "results": results,
        "tags": {"state": "N/A", "reason": "no existing tag retrieval interface"},
        "moc_associations": {"state": "N/A", "reason": "no existing MOC retrieval ranking"},
        "graph_quality": "NOT RUN",
        "model_calls": 0,
    }
