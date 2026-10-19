"""Complete text coverage, fixed offline vector recall, and source-scoped caches."""

from dataclasses import asdict
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from .. import database
from ..config import InvalidInput
from ..inputs import canonical
from ..knowledge_graph import policy, vectors, worker
from ..knowledge_graph.contracts import Request, Source
from ..objects import digest
from . import store, text_features


def chunks(text: str, size: int = 110) -> list[dict[str, Any]]:
    result = []
    for part in text_features.passages(text):
        for offset in range(0, len(part["text"]), size):
            start = part["start"] + offset
            end = min(part["end"], start + size)
            if text[start:end].strip():
                result.append({"start": start, "end": end, "text": text[start:end]})
    return result


def recall(
    config: Any, job: Any, entity: Any, mocs: Any, check: Any, *, cached_only: bool = False
) -> Any:
    profiles = [text_features.moc_profile(m) for m in mocs]
    profiles = [p for p in profiles if not p["navigation"]]
    profile = policy.current(config)
    if profile is None or not profile.enabled or profile.engine.model is None:
        return {"state": "disabled", "candidates": [p["id"] for p in profiles]}
    grouped = {"source": chunks(entity["text"])}
    grouped.update({p["id"]: chunks(p["text"]) for p in profiles})
    titles = {"source": entity["title"], **{p["id"]: p["title"] for p in profiles}}
    sources = {}
    groups: dict[str, list[str]] = {}
    for group, parts in grouped.items():
        groups[group] = []
        for part in parts:
            # Short derived fragments make the pinned graph worker's bounded
            # sampler cover every paragraph, rather than only three snippets.
            title = titles[group][:80]
            text = "# " + title + "\n内容：" + part["text"]
            h = digest(text.encode())
            identity = "note:" + str(uuid5(NAMESPACE_URL, h))
            sources[identity] = Source(identity, h, h, title, text, "historical_note", None)
            groups[group].append(identity)
    config_sha = digest(canonical(asdict(profile.engine)).encode())
    cached = store.get_many(config, "vectors", [config_sha + ":" + k for k in sources])
    cache = {k: cached[config_sha + ":" + k] for k in sources if config_sha + ":" + k in cached}
    identities = sorted(sources)
    for begin in range(0, len(identities), 100):
        subset = identities[begin : begin + 100]
        request = Request(
            str(uuid4()),
            job["client_id"],
            job["vault_id"],
            profile.engine,
            tuple(sources[k] for k in subset),
        )
        vectors.validate({key: cache[key] for key in subset if key in cache}, request)
    missing = sorted(set(sources) - set(cache))
    if missing and cached_only:
        raise InvalidInput("organization_model_cache_incomplete")
    embedded = 0
    for begin in range(0, len(missing), 100):
        check()
        request = Request(
            str(uuid4()),
            job["client_id"],
            job["vault_id"],
            profile.engine,
            tuple(sources[k] for k in missing[begin : begin + 100]),
        )
        raw = worker.execute_payload(
            worker.Policy(profile.image, profile.timeout_seconds),
            request.task_id,
            vectors.encode(request, {}),
            check,
        )
        result = vectors.decode_build(raw, request, {})
        check()
        with database.locked(config):
            store.put_many(
                config,
                "vectors",
                job["vault_id"],
                {config_sha + ":" + identity: row for identity, row in result.vectors.items()},
            )
        cache.update(result.vectors)
        embedded += result.embedded_count
    # Scores recall subjects only; they never grant a write or establish truth.
    ranked = []
    for p in profiles:
        scores = []
        for source in groups["source"]:
            left = cache[source]["values"]
            scores.append(
                max(
                    (
                        sum(a * b for a, b in zip(left, cache[right]["values"], strict=True))
                        for right in groups[p["id"]]
                    ),
                    default=0,
                )
            )
        top = sorted(scores, reverse=True)[:3]
        ranked.append({"moc_id": p["id"], "score": round(sum(top) / len(top), 5) if top else 0})
    ranked.sort(key=lambda r: (-r["score"], r["moc_id"]))
    return {
        "state": "ready",
        "configuration_sha256": config_sha,
        "embedded": embedded,
        "source_chunks": len(groups["source"]),
        "candidates": [r["moc_id"] for r in ranked],
        "scores": ranked,
    }
