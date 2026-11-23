"""A current read proof remains ephemeral and does not invalidate rollback-compatible caches."""

import json

from test_note_search_cache import indexed_scope as indexed_scope

from fiber_bundle import note_index, note_search_build, note_search_cache


def test_current_read_signature_is_not_persisted_in_the_existing_index_schema(
    http, config, indexed_scope
):
    cid, _, vid, root = indexed_scope
    current = note_index.read_note(root, "one.md", 10000)
    assert current.read_signature is not None
    assert set(note_index.metadata(current)) == {
        "relative_path",
        "sha256",
        "title",
        "text",
        "publication_id",
        "file_key",
        "identity",
        "publication_path",
    }
    assert note_search_build.build(config, cid, vid)["state"] == "ready"
    with note_search_cache.connection(config, vid) as index:
        assert index is not None
        rows = index.execute("SELECT metadata FROM notes").fetchall()
    assert rows
    assert all("read_signature" not in json.loads(row[0]) for row in rows)
