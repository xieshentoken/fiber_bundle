"""Bounded adaptive retrieval, frozen recipes, and deterministic source revalidation."""

from typing import Any

from .. import evidence_answers
from ..config import InvalidInput
from ..search_matching import terms
from ..search_snapshot import SearchSnapshot, combine
from . import evidence_passages, query, retrieval_terms

SCHEMA = "channel-retrieval/v4"
COMBINED_SCHEMAS = {"channel-retrieval/v3", SCHEMA}
INITIAL_ATTEMPTS = 8
NOTE_LIMIT = 5


def _recipe(value: Any) -> dict[str, Any]:
    expected = {"schema", "initial", "attempted", "contributing", "supplement"}
    if isinstance(value, dict) and value.get("schema") in COMBINED_SCHEMAS:
        expected.add("preserved")
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value["schema"] not in {*COMBINED_SCHEMAS, "channel-retrieval/v2"}
        or type(value["supplement"]) is not bool
    ):
        raise InvalidInput("channel_source_changed")
    for key, maximum in (("initial", 3), ("attempted", retrieval_terms.MAX_ATTEMPTS)):
        items = value[key]
        if not isinstance(items, list) or not 1 <= len(items) <= maximum:
            raise InvalidInput("channel_source_changed")
        for expression in items:
            terms(expression)
        if len(items) != len(set(items)):
            raise InvalidInput("channel_source_changed")
    contributed = value["contributing"]
    if not isinstance(contributed, list) or any(q not in value["attempted"] for q in contributed):
        raise InvalidInput("channel_source_changed")
    if len(contributed) != len(set(contributed)):
        raise InvalidInput("channel_source_changed")
    preserved = value.get("preserved", [])
    if (
        not isinstance(preserved, list)
        or len(preserved) > NOTE_LIMIT
        or any(
            not isinstance(key, list)
            or len(key) != 2
            or any(not isinstance(part, str) or not part for part in key)
            for key in preserved
        )
        or len({tuple(key) for key in preserved}) != len(preserved)
    ):
        raise InvalidInput("channel_source_changed")
    return value


def retrieve(
    config: Any,
    db: Any,
    channel: Any,
    question: str,
    initial: list[str],
    *,
    recipe: dict[str, Any] | None = None,
    supplement: bool = False,
    previous: list[str] | None = None,
    schema: str = SCHEMA,
    preserved: list[list[str]] | None = None,
) -> dict[str, Any]:
    with SearchSnapshot(config, db, channel["value"]["client_id"]) as snapshot:
        return _retrieve(
            config,
            db,
            channel,
            question,
            initial,
            snapshot,
            recipe=recipe,
            supplement=supplement,
            previous=previous,
            schema=recipe["schema"] if recipe else schema,
            preserved=recipe.get("preserved", []) if recipe else preserved or [],
        )


def _retrieve(
    config: Any,
    db: Any,
    channel: Any,
    question: str,
    initial: list[str],
    snapshot: SearchSnapshot,
    *,
    recipe: dict[str, Any] | None,
    supplement: bool,
    previous: list[str] | None,
    schema: str,
    preserved: list[list[str]],
) -> dict[str, Any]:
    combined = schema in COMBINED_SCHEMAS
    sources: list[dict[str, Any]] = []
    notes: set[tuple[str, str]] = set()
    used: list[str] = []
    attempted: list[str] = []
    packets = []
    origins: dict[tuple[str, str], str] = {}
    maximum = retrieval_terms.MAX_ATTEMPTS if supplement or recipe else INITIAL_ATTEMPTS

    def collect(expressions: list[str]) -> None:
        for expression in expressions:
            if len(attempted) >= maximum or (not combined and len(notes) >= NOTE_LIMIT):
                break
            if expression in attempted:
                continue
            attempted.append(expression)
            packet = query.evidence(
                config,
                db,
                channel,
                question,
                expression,
                snapshot=snapshot,
                combine_sources=combined,
            )
            packets.append(packet)
            before = len(sources)
            for item in packet["sources"]:
                key = (item["vault_id"], item["note_path"])
                if key in notes or (not combined and len(notes) >= NOTE_LIMIT):
                    continue
                selections = evidence_passages.select(
                    config,
                    db,
                    channel,
                    item,
                    expression,
                    question,
                    3 if supplement else 2,
                    procedure_context=schema == SCHEMA,
                )
                if selections:
                    notes.add(key)
                    origins[key] = expression
                for selected in selections:
                    sources.append({**selected, "citation": f"[{len(sources) + 1}]"})
            if len(sources) > before:
                used.append(expression)

    # Revalidate contributing searches with their full current-file/index guards.
    # Repeating failed discovery queries at every model checkpoint adds scans,
    # not source validation. Keep all attempts separately for the total bound.
    collect((recipe["contributing"] or recipe["attempted"]) if recipe else previous or initial)
    target = NOTE_LIMIT if supplement else 3
    if recipe is None and len(notes) < target:
        segmented, relaxed = retrieval_terms.fallbacks(
            initial, retrieval_terms.authorized_titles(config, db, channel, snapshot)
        )
        collect(segmented)
        if len(notes) < target:
            collect(relaxed)
        if len(notes) < target:
            # Broaden the condition while retaining the leading topic. A generic
            # trailing word (e.g. "standard") must never become its own query.
            collect(list(dict.fromkeys(term.split()[0] for term in [*initial, *segmented])))
    if combined:
        representatives = {(s["vault_id"], s["note_path"]): s for s in sources}
        mixed = combine(
            [s for s in representatives.values() if s.get("expert_projection")],
            [s for s in representatives.values() if not s.get("expert_projection")],
            NOTE_LIMIT,
        )
        keys = [(key[0], key[1]) for key in preserved]
        if any(key not in representatives for key in keys):
            raise InvalidInput("channel_source_changed")
        keys += [
            (s["vault_id"], s["note_path"])
            for s in mixed
            if (s["vault_id"], s["note_path"]) not in keys
        ]
        keys = keys[:NOTE_LIMIT]
        sources = [s for key in keys for s in sources if (s["vault_id"], s["note_path"]) == key]
        sources = [{**s, "citation": f"[{i}]"} for i, s in enumerate(sources, 1)]
        used = [expression for expression in used if expression in {origins[k] for k in keys}]
    response = packets[0]
    response.update(
        sources=sources,
        quotations=[{"citation": s["citation"], "quote": s["snippet"]} for s in sources],
        comparisons=evidence_answers.comparisons(sources),
        retrieval_queries=used or initial,
        retrieval_recipe={
            "schema": schema,
            "initial": initial,
            "attempted": attempted,
            "contributing": used,
            "supplement": supplement,
            **({"preserved": preserved} if combined else {}),
        },
        status="evidence_only" if sources else "insufficient_evidence",
    )
    response.pop("expert_blocks", None)
    if sources:
        response["answer"] = "找到以下原文证据；尚不能据此给出确定结论。"
    from ..expert_answers import enrich

    return enrich(config, db, channel["value"]["client_id"], question, response)


def replay(config: Any, db: Any, channel: Any, source: Any) -> dict[str, Any]:
    recipe = _recipe(source["retrieval_recipe"])
    return retrieve(
        config,
        db,
        channel,
        source["question"],
        recipe["initial"],
        recipe=recipe,
        supplement=recipe["supplement"],
    )


def expand(config: Any, db: Any, channel: Any, source: Any) -> dict[str, Any] | None:
    """One expansion after abstention; historical frozen requests are never upgraded."""
    if "retrieval_recipe" not in source:
        return None
    recipe = _recipe(source["retrieval_recipe"])
    if recipe["supplement"]:
        return None
    expanded = retrieve(
        config,
        db,
        channel,
        source["question"],
        recipe["initial"],
        supplement=True,
        previous=recipe["attempted"],
        schema=recipe["schema"],
        preserved=[
            list(key)
            for key in dict.fromkeys((s["vault_id"], s["note_path"]) for s in source["sources"])
        ],
    )
    before = {(s["vault_id"], s["note_path"], s["sha256"], s["snippet"]) for s in source["sources"]}
    after = {
        (s["vault_id"], s["note_path"], s["sha256"], s["snippet"]) for s in expanded["sources"]
    }
    if not {s[:3] for s in before} <= {s[:3] for s in after}:
        raise InvalidInput("channel_source_changed")
    return expanded if after - before else None
