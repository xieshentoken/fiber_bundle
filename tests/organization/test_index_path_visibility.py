"""Unrelated registered packages preserve every admitted corpus row through each slice."""

import pytest
from test_vault_sync_reconcile import observe

from fiber_bundle import auth, database, indexed_sources, note_search_cache, search
from fiber_bundle.organization import packages
from fiber_bundle.vault_sync import index, scanner, store, visibility


def test_sixty_unrelated_notes_remain_searchable_during_every_package_publication_slice(
    config, scope, package, monkeypatch
):
    client, vault, root = scope
    (root / "Corpus").mkdir()
    paths = {f"Corpus/u-{n:02}.md": f"stabletoken{n:03}" for n in range(60)}
    for path, term in paths.items():
        (root / path).write_text(f"# Corpus\n{term} unchanged content.")
    observe(config, scope)

    def build():
        scanner.heartbeat(config, vault)
        return index.update(config, client, vault)

    while build()["pending_notes"]:
        pass
    original = {p: (root / p).read_bytes() for p in paths}
    with database.connect(config.state_root) as db:
        plan = packages.inspect(config, db, client, vault, package.relative_to(root).as_posix())
        packages.commit(config, db, client, vault, plan, notify=False)

    def verify():
        with database.connect(config.state_root) as db:
            row = auth.grant(db, client, vault, "query")
            view = indexed_sources.read(config, db, client, row)
            assert set(paths) <= set(view.notes)
            for path, term in paths.items():
                result = search.search(config, db, client, term, [vault])
                assert any(r["note_path"] == path for r in result["results"])
        with note_search_cache.connection(config, vault) as db:
            assert set(paths) <= {r[0] for r in db.execute("SELECT path FROM chunks")}

    verify()  # Registration itself must not hide the 60 validated notes.
    for n in range(13):
        (root / f"Corpus/new-{n:02}.md").write_text("additional unrelated note")
    store.enqueue(config, vault, [("Corpus", 1)], "17", now=20)
    while tasks := store.due(config, now=30):
        for task in tasks:
            scanner.apply(config, task, now=30)
    monkeypatch.setattr(index, "BATCH", 7)
    publications = 0
    while True:
        result = build()
        publications += 1
        verify()
        if not result["pending_notes"]:
            break
    assert publications == 2
    assert all((root / p).read_bytes() == value for p, value in original.items())


@pytest.mark.parametrize("change", ["unknown", "root", "scope"])
def test_untrusted_authority_proof_is_never_query_admitted(config, scope, change):
    client, vault, root = scope
    (root / "note.md").write_text("stable sentinel")
    observe(config, scope)
    scanner.heartbeat(config, vault)
    index.update(config, client, vault)
    location = note_search_cache.location(config, vault)
    import sqlite3

    with sqlite3.connect(location) as db:
        if change == "unknown":
            db.execute("DELETE FROM sync_projection")
        else:
            old = dict(db.execute("SELECT path,proof FROM sync_projection"))
            db.execute(
                "UPDATE sync_projection SET proof=? WHERE path=''",
                (visibility.digest([change, old[""]]),),
            )
    with database.connect(config.state_root) as db:
        row = auth.grant(db, client, vault, "query")
        view = indexed_sources.read(config, db, client, row)
    assert not view.notes
    assert not view.coverage["complete"]
