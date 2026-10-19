"""Worker-only Semantica integration: title mentions and semantic clues, never facts."""

import importlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import InvalidInput
from .contracts import SCHEMA, Request, Source
from .results import Span, decode_result, entity_id, line_at, relation_id
from .runtime_context import ALGORITHM, GRAPH_MINIMUM_SCORE, NEIGHBOR_LIMIT, verify
from .sampling import MODEL_TOKENS, body_sample
from .topics import overlapping_topics


def _span(note: Source, start: int, end: int) -> Span:
    return Span(
        note.note_id,
        note.sha256,
        start,
        end,
        line_at(note.text, start),
        line_at(note.text, end - 1),
        note.text[start:end],
    )


def _embeddings(request: Request, model_root: Path, texts: list[str]) -> Any:
    # Imports stay behind opt-in and immutable artifact checks. Nothing calls an LLM/provider.
    np = importlib.import_module("numpy")
    semantica = importlib.import_module("semantica")
    torch = importlib.import_module("torch")
    TextEmbedder = importlib.import_module("semantica.embeddings.text_embedder").TextEmbedder
    if semantica.__version__ != request.engine.version:
        raise InvalidInput("graph_sdk_version_mismatch")
    torch.set_num_threads(1)
    embedder = TextEmbedder(
        model_name=str(model_root), method="sentence_transformers", device="cpu"
    )
    model = request.engine.model
    assert model is not None
    if (
        embedder.get_method() != "sentence_transformers"
        or embedder.get_embedding_dimension() != model.dimensions
    ):
        raise InvalidInput("graph_real_vector_backend_unavailable")
    tokenizer = importlib.import_module("transformers").AutoTokenizer.from_pretrained(
        str(model_root), local_files_only=True
    )
    configured_limit = json.loads((model_root / "sentence_bert_config.json").read_text())[
        "max_seq_length"
    ]
    if not isinstance(configured_limit, int) or configured_limit < 1:
        raise InvalidInput("graph_model_input_limit")
    limit = min(MODEL_TOKENS, configured_limit, tokenizer.model_max_length)
    if any(len(tokenizer.encode(text, truncation=False)) > limit for text in texts):
        raise InvalidInput("graph_model_input_limit")
    vectors = np.asarray(embedder.embed_batch(texts), dtype=np.float64)
    if (
        embedder.get_method() != "sentence_transformers"
        or vectors.shape != (len(texts), model.dimensions)
        or not np.isfinite(vectors).all()
        or not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=0.001)
    ):
        raise InvalidInput("graph_invalid_real_vectors")
    return vectors


def analyze_cached(
    request: Request,
    context_path: Path,
    model_root: Path,
    cache: dict[str, list[float]],
) -> tuple[dict[str, Any], dict[str, list[float]], int]:
    request.validate()
    if request.engine.name != "semantica" or request.engine.algorithm != ALGORITHM:
        raise InvalidInput("graph_adapter_unsupported")
    verify(context_path, model_root, request.engine.model)
    GraphBuilder = importlib.import_module("semantica.kg.graph_builder").GraphBuilder
    CommunityDetector = importlib.import_module("semantica.kg.community_detector").CommunityDetector
    entities: list[dict[str, Any]] = []
    samples = [body_sample(note) for note in request.notes]
    excerpts: list[Span] = []
    for note in request.notes:
        start = note.text.find(note.title)
        if start < 0:
            raise InvalidInput("graph_title_mention_unavailable")
        mention = _span(note, start, start + len(note.title))
        excerpts.append(samples[len(excerpts)].evidence[0])
        entities.append(
            {"id": entity_id(note.title, mention), "label": note.title, "evidence": asdict(mention)}
        )
    missing = [i for i, note in enumerate(request.notes) if note.note_id not in cache]
    fresh = _embeddings(request, model_root, [samples[i].text for i in missing]) if missing else []
    all_vectors = {
        **cache,
        **{
            request.notes[i].note_id: list(map(float, v))
            for i, v in zip(missing, fresh, strict=True)
        },
    }
    np = importlib.import_module("numpy")
    vectors = np.asarray([all_vectors[n.note_id] for n in request.notes], dtype=np.float64)
    scores = vectors @ vectors.T
    relations: list[dict[str, Any]] = []
    pairs = set()
    for source in range(len(entities)):
        targets = sorted(
            (target for target in range(len(entities)) if target != source),
            key=lambda target: (-float(scores[source, target]), entities[target]["id"]),
        )[:NEIGHBOR_LIMIT]
        for target in targets:
            pair = tuple(sorted((source, target)))
            score = float(scores[source, target])
            if pair in pairs or score < GRAPH_MINIMUM_SCORE:
                continue
            pairs.add(pair)
            left, right = pair
            evidence = samples[left].evidence + samples[right].evidence
            a, b = entities[left]["id"], entities[right]["id"]
            relations.append(
                {
                    "id": relation_id(a, b, "similarity", evidence),
                    "source": a,
                    "target": b,
                    "kind": "similarity",
                    "derivation": "semantic_similarity",
                    "method": ALGORITHM,
                    "score": min(1.0, score),
                    "score_kind": "model_score",
                    "evidence": [asdict(span) for span in evidence],
                }
            )
    builder = GraphBuilder(
        merge_entities=False, resolve_conflicts=False, unknown_relation_endpoint="reject"
    )
    graph = builder.build(
        {
            "entities": [{"id": entity["id"], "name": entity["label"]} for entity in entities],
            "relationships": [
                {"source": edge["source"], "target": edge["target"], "type": "similarity"}
                for edge in relations
            ],
        },
        extract=False,
    )
    expected_nodes = {entity["id"] for entity in entities}
    expected_edges = {(edge["source"], edge["target"]) for edge in relations}
    if (
        {node["id"] for node in graph["entities"]} != expected_nodes
        or {(edge["source"], edge["target"]) for edge in graph["relationships"]} != expected_edges
        or graph["metadata"]["entity_resolution_applied"] is not False
        or graph["metadata"]["rejected_relationships"] != 0
    ):
        raise InvalidInput("graph_builder_changed_candidates")
    detector = CommunityDetector()
    if not detector.use_networkx:
        raise InvalidInput("graph_community_backend_unavailable")
    communities = (
        detector.detect_communities(graph, algorithm="louvain", seed=0)["communities"]
        if relations
        else []
    )
    by_id = {
        entity["id"]: (entity, excerpt) for entity, excerpt in zip(entities, excerpts, strict=True)
    }
    mocs = []
    topic_vectors = {
        entity["id"]: all_vectors[note.note_id]
        for entity, note in zip(entities, request.notes, strict=True)
    }
    for community in overlapping_topics(communities, topic_vectors):
        if not 2 <= len(community) <= len(request.notes):
            continue
        members = [by_id[node] for node in sorted(community)]
        mocs.append(
            {
                "title": "关联候选：" + members[0][0]["label"][:80],
                "note_ids": sorted(member[1].note_id for member in members),
                "evidence": [asdict(member[1]) for member in members],
                "method": ALGORITHM,
            }
        )
    value = {
        "schema": SCHEMA,
        "task_id": request.task_id,
        "input_sha256": request.input_sha256,
        "configuration_sha256": request.configuration_sha256,
        "engine": {
            "name": request.engine.name,
            "version": request.engine.version,
            "revision": request.engine.revision,
        },
        "entities": entities,
        "relations": relations,
        "mocs": mocs,
    }
    from ..inputs import canonical

    decode_result(canonical(value).encode(), request)
    return value, all_vectors, len(missing)


def analyze(request: Request, context_path: Path, model_root: Path) -> dict[str, Any]:
    return analyze_cached(request, context_path, model_root, {})[0]


def benchmark_scores(
    request: Request,
    context_path: Path,
    model_root: Path,
    queries: tuple[str, ...],
    *,
    enabled: bool = False,
) -> list[list[float]]:
    """Isolated S1 benchmark only; no production retrieval API or persistence."""
    from .contracts import bounded_text

    if enabled is not True:
        raise InvalidInput("knowledge_graph_disabled")
    request.validate()
    if not 1 <= len(queries) <= 20 or request.engine.algorithm != ALGORITHM:
        raise InvalidInput("graph_benchmark_scope")
    for query in queries:
        bounded_text(query, 200)
    verify(context_path, model_root, request.engine.model)
    documents = [body_sample(note).text for note in request.notes]
    texts = documents + ["为这个句子生成表示以用于检索相关文章：" + query for query in queries]
    vectors = _embeddings(request, model_root, texts)
    scores = vectors[len(documents) :] @ vectors[: len(documents)].T
    return [[float(score) for score in row] for row in scores]


def query_scores(
    request: Request,
    context_path: Path,
    model_root: Path,
    query: str,
    cache: dict[str, list[float]],
) -> list[float]:
    if request.engine.algorithm != ALGORITHM:
        raise InvalidInput("graph_adapter_unsupported")
    verify(context_path, model_root, request.engine.model)
    vector = _embeddings(request, model_root, ["为这个句子生成表示以用于检索相关文章：" + query])[0]
    np = importlib.import_module("numpy")
    matrix = np.asarray([cache[n.note_id] for n in request.notes], dtype=np.float64)
    return [float(score) for score in matrix @ vector]
