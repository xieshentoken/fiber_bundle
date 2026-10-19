"""Opt-in internal recall. Current source evidence stays in the existing literal response."""

import subprocess
from typing import Any

from .. import auth, database, search
from ..config import Config, InvalidInput, StorageError
from . import catalog, intake, jobs, store, worker
from .contracts import MAX_OUTPUT_BYTES, bounded_text, read_json
from .evaluation import eligible
from .runtime_context import QUERY_MINIMUM_SCORE


def recall(
    config: Config, cid: str, vid: str, query: str, *, enabled: bool = False
) -> dict[str, Any]:
    bounded_text(query, 200)
    with database.locked(config):
        jobs.authorize(config, cid, vid)
        db = database.connect(config.state_root)
        try:
            literal = search.vault_search(config, db, cid, query, [vid], 10)
        finally:
            db.close()
        if enabled is not True:
            return {**literal, "graph_state": "disabled", "graph_candidates": []}
        value = catalog.generation(config, cid, vid)
        if value is None or not value.visible:
            return {**literal, "graph_state": "unavailable", "graph_candidates": []}
        selected = tuple(
            intake.Selection(n.note_id, n.sha256)
            for n in value.prepared.request.notes
            if n.note_id in value.visible
        )
        prepared = intake.prepare(
            config,
            None,
            vid,
            selected,
            value.profile.engine,
            enabled=True,
            client_id=cid,
            _locked=True,
        )
        with store.connection(config) as side:
            assert side is not None
            row = store.job(side, value.job_id, cid, vid)
            cached = read_json(row["vectors_json"].encode(), MAX_OUTPUT_BYTES)
            cache = {
                n.note_id: cached[n.note_id] for n in prepared.request.notes if n.note_id in cached
            }
        # Freeze only current authorized nodes before any vector scoring or topology traversal.
        allowed = {n.note_id for n in prepared.request.notes}
        entities = {
            e.id: e.evidence.note_id for e in value.result.entities if e.evidence.note_id in allowed
        }
        seeds = {hit.get("note_id") for hit in literal["results"]} & allowed
        adjacent: dict[str, list[str]] = {}
        with store.connection(config) as side:
            assert side is not None
            for edge in value.result.relations:
                if edge.source not in entities or edge.target not in entities:
                    continue
                if not {s.note_id for s in edge.evidence} <= allowed:
                    continue
                if (
                    catalog.decision(side, cid, vid, value.profile.digest, edge.id)["state"]
                    == "rejected"
                ):
                    continue
                a, b = entities[edge.source], entities[edge.target]
                if a in seeds:
                    adjacent.setdefault(b, []).append(edge.id)
                if b in seeds:
                    adjacent.setdefault(a, []).append(edge.id)
    try:
        scores = worker.run_query(
            config,
            prepared,
            worker.Policy(value.profile.image, min(30, value.profile.timeout_seconds)),
            query,
            cache,
            check=lambda: jobs._check(config, prepared, value.profile),
        )
        with database.locked(config):
            jobs._check(config, prepared, value.profile, _locked=True)
            current = catalog.generation(config, cid, vid)
            if current is None or current.job_id != value.job_id:
                raise InvalidInput("graph_generation_changed")
            # Re-read base evidence after inference; never return the pre-inference snapshot.
            db = database.connect(config.state_root)
            try:
                literal = search.vault_search(config, db, cid, query, [vid], 10)
            finally:
                db.close()
            known = {hit.get("note_id") for hit in literal["results"]}
            rows = []
            for i in sorted(
                range(len(scores)), key=lambda i: (-scores[i], prepared.request.notes[i].note_id)
            ):
                source = prepared.request.notes[i]
                if source.note_id in known or not eligible(query, source.text):
                    continue
                if scores[i] < QUERY_MINIMUM_SCORE and source.note_id not in adjacent:
                    continue
                rows.append(
                    {
                        "note_id": source.note_id,
                        "note_path": current.paths[source.note_id],
                        "sha256": source.sha256,
                        "title": source.title,
                        "source_kind": source.source_kind,
                        "source_version_id": source.source_version_id,
                        "score": scores[i],
                        "score_kind": "model_score",
                        "method": value.profile.engine.algorithm,
                        "reason": "语义相似线索"
                        if source.note_id not in adjacent
                        else "一跳关联线索",
                        "relation_ids": adjacent.get(source.note_id, [])[:20],
                        "evidence_kind": "retrieval_clue",
                    }
                )
                if len(rows) == 10:
                    break
            return {**literal, "graph_state": "ready", "graph_candidates": rows, "graph_depth": 1}
    except (StorageError, OSError, auth.Denied, subprocess.SubprocessError):
        # Permission errors must not fall back to a now-unauthorized pre-inference response.
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                literal = search.vault_search(config, db, cid, query, [vid], 10)
            finally:
                db.close()
        return {
            **literal,
            "graph_state": "fallback",
            "graph_reason": "graph_recall_unavailable",
            "graph_candidates": [],
        }
