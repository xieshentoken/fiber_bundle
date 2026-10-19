from math import sqrt

from fiber_bundle.knowledge_graph.topics import overlapping_topics


def test_bridge_is_in_two_distinct_topics_without_unrelated_note():
    vectors = {
        "a1": [1.0, 0.0, 0.0],
        "a2": [1.0, 0.0, 0.0],
        "b1": [0.0, 1.0, 0.0],
        "b2": [0.0, 1.0, 0.0],
        "bridge": [1 / sqrt(2), 1 / sqrt(2), 0.0],
        "other": [0.0, 0.0, 1.0],
    }
    topics = overlapping_topics([["a2", "a1"], ["b1", "b2"]], vectors)
    assert topics == [["a1", "a2", "bridge"], ["b1", "b2", "bridge"]]
    assert overlapping_topics([["a1", "a2"], ["a2", "a1"]], vectors) == [topics[0]]


def test_expansion_is_bounded_and_deterministic_not_iterative():
    vectors = {str(i): [1.0, 0.0] for i in range(100)}
    topics = overlapping_topics([["0", "1"]], vectors)
    assert len(topics) == 1 and len(topics[0]) == 22
    assert topics == overlapping_topics([["1", "0"]], dict(reversed(list(vectors.items()))))
    assert overlapping_topics([["0"]], vectors) == []
