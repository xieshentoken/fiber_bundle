"""Independent, bounded memberships around fixed community seeds; no iterative merging."""

from math import sqrt

from .runtime_context import GRAPH_MINIMUM_SCORE, NEIGHBOR_LIMIT


def overlapping_topics(
    communities: list[list[str]], vectors: dict[str, list[float]]
) -> list[list[str]]:
    topics: set[tuple[str, ...]] = set()
    for community in communities:
        seed = sorted(set(community))
        if len(seed) < 2:
            continue
        center = [sum(vectors[node][i] for node in seed) for i in range(len(vectors[seed[0]]))]
        norm = sqrt(sum(x * x for x in center))
        if norm == 0:
            continue
        center = [x / norm for x in center]
        scored = [
            (sum(a * b for a, b in zip(center, vector, strict=True)), node)
            for node, vector in vectors.items()
            if node not in seed
        ]
        additions = [
            node
            for score, node in sorted(scored, key=lambda row: (-row[0], row[1]))
            if score >= GRAPH_MINIMUM_SCORE
        ][:NEIGHBOR_LIMIT]
        topics.add(tuple(sorted(seed + additions)))
    return [list(topic) for topic in sorted(topics)]
