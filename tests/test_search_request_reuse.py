"""Request-local reuse preserves search results and current-evidence rejection."""

from contextlib import contextmanager

from test_note_search_cache import indexed_scope as indexed_scope
from test_search import retrieve

from fiber_bundle import note_identity_store, note_search_build, note_search_cache


def test_many_matching_chunks_read_each_metadata_once(http, config, indexed_scope, monkeypatch):
    cid, headers, vid, root = indexed_scope
    (root / "many.md").write_text("# 压力实验\n" + "压力变化须核对。\n" * 120)
    expected = retrieve(http, headers, "压力", [vid]).json()["results"]
    note_search_build.build(config, cid, vid)
    original = note_search_cache.connection
    statements = []

    @contextmanager
    def traced(*args, **kwargs):
        with original(*args, **kwargs) as db:
            if db is not None:
                db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(note_search_cache, "connection", traced)
    response = retrieve(http, headers, "压力", [vid])
    assert response.status_code == 200
    assert response.json()["results"] == expected
    reads = [s for s in statements if s.startswith("SELECT metadata FROM notes WHERE path=")]
    assert len(reads) == len(set(reads)) == 4


def test_delivery_shares_registry_but_retains_final_fingerprint(
    http, config, indexed_scope, monkeypatch
):
    cid, headers, vid, root = indexed_scope
    note_search_build.build(config, cid, vid)
    original = note_identity_store.records
    reads = []

    def observed(c, vault):
        reads.append(vault)
        return original(c, vault)

    monkeypatch.setattr(note_identity_store, "records", observed)
    response = retrieve(http, headers, "压力", [vid])
    assert response.status_code == 200 and len(response.json()["results"]) == 3
    # Initial fingerprint, shared delivery registration, final fingerprint.
    assert reads == [vid, vid, vid]


def test_many_matching_notes_project_intake_once_without_per_note_json_scan(
    http, config, indexed_scope, monkeypatch
):
    from fiber_bundle import database, search_provenance

    cid, headers, vid, root = indexed_scope
    for i in range(25):
        (root / f"many-{i:02d}.md").write_text(f"# 压力实验{i}\n压力证据。\n")
    note_search_build.build(config, cid, vid)
    original = search_provenance.intake_locations
    calls = []
    statements = []
    connect = database.connect

    def traced(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    def observed(db, vault_id):
        calls.append(vault_id)
        return original(db, vault_id)

    monkeypatch.setattr(database, "connect", traced)
    monkeypatch.setattr(search_provenance, "intake_locations", observed)
    response = retrieve(http, headers, "压力", [vid])
    assert response.status_code == 200 and len(response.json()["results"]) == 10
    assert calls == [vid]
    assert not any("json_each(l.value,'$.published_hashes')" in sql for sql in statements)
