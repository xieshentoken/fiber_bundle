"""Exact former proofs bridge once; changed or missing authority never gets inferred."""

import sqlite3
import time
from collections import defaultdict

import pytest
from test_vault_sync_reconcile import observe

from fiber_bundle import (
    auth,
    database,
    indexed_sources,
    note_identities,
    note_identity_store,
    note_search_cache,
    publication_locations,
    search,
    vault_scope,
    vaults,
)
from fiber_bundle.attachment_projection import hidden_roots
from fiber_bundle.document_creation_save import inventory_paths
from fiber_bundle.objects import digest
from fiber_bundle.organization import packages
from fiber_bundle.vault_merge_guard import retained_paths
from fiber_bundle.vault_sync import index, scanner, store, visibility


def legacy_read(config, db, vault):
    # Frozen former read algorithm, independent of the new compatibility hint.
    root = vaults.checked_vault(config, vault)
    scope = vault_scope.for_vault(root, vault)
    layout = publication_locations.project_inventory(db, vault["id"], include_attachments=True)
    context = visibility.digest(
        {
            "root": str(root),
            "scope": scope.wire(),
            "experts": sorted(packages.roots(config, vault["id"])),
            "hidden_roots": sorted(
                layout.hidden_roots
                | hidden_roots(db, vault["id"], location_roots=layout.attachment_roots)
            ),
            "retained": sorted(retained_paths(db, vault["id"])),
        }
    )
    values = defaultdict(list)
    for path, value in sorted(layout.managed.items()):
        values[path].append(["managed", value])
    for path in sorted(layout.hidden):
        values[path].append(["hidden"])
    queries = {
        "locations": "SELECT entry_note_path,kind,publication_id,state,package_root,revision "
        "FROM publication_locations WHERE vault_id=? ORDER BY kind,publication_id",
        "articles": "SELECT p.relative_path,p.id,p.state,p.version_id FROM article_publications p "
        "LEFT JOIN publication_locations l ON l.kind='article' AND l.publication_id=p.id "
        "WHERE COALESCE(l.vault_id,p.vault_id)=? ORDER BY p.id",
        "documents": "SELECT p.relative_path,p.run_id,p.state FROM document_publications p "
        "JOIN execution_runs r ON r.id=p.run_id WHERE r.vault_id=? ORDER BY p.run_id",
        "media": "SELECT p.relative_path,p.job_id,p.state FROM media_publications p "
        "JOIN media_tasks m ON m.id=p.job_id WHERE m.vault_id=? ORDER BY p.job_id",
        "expert_publications": "SELECT p.relative_path,p.task_id,p.state "
        "FROM source_expert_publications p "
        "JOIN source_imports i ON i.id=p.task_id WHERE i.vault_id=? ORDER BY p.task_id",
    }
    for kind, sql in queries.items():
        for row in db.execute(sql, (vault["id"],)):
            values[row[0]].append([kind, *tuple(row)[1:]])
    for record in note_identity_store.records(config, vault["id"]):
        values[record.relative_path].append(
            ["identity", record.identity, record.publication_id, record.vault_key, record.file_key]
        )
    analysis, published = inventory_paths(db, vault["id"])
    for path in sorted(analysis | published):
        values[path].append(["analysis", path in published])
    return visibility.Proof(
        context, {path: visibility.digest(value) for path, value in values.items()}
    )


def build(config, scope):
    scanner.heartbeat(config, scope[1])
    return index.update(config, scope[0], scope[1])


def install_former_proof(config, scope):
    client, vault, _ = scope
    with database.connect(config.state_root) as db:
        old = legacy_read(config, db, auth.grant(db, client, vault, "query"))
    indexed = store.library(config, vault)["indexed"]
    assert indexed >= 0
    with sqlite3.connect(note_search_cache.location(config, vault)) as db:
        visibility.save(db, old)
        db.execute("UPDATE sync_metadata SET visibility=?", (old.token,))
        db.execute(
            "UPDATE generation SET fingerprint=?", (visibility.digest([vault, indexed, old.token]),)
        )
    return old


def corpus(config, scope):
    _, _, root = scope
    (root / "Corpus").mkdir()
    paths = {f"Corpus/u-{n:02}.md": f"oldstabletoken{n:03}" for n in range(60)}
    for path, term in paths.items():
        (root / path).write_text(f"# Existing\n{term} unchanged body.")
    (root / "tracked.md").write_text("tracked outside the unrelated corpus")
    observe(config, scope)
    while build(config, scope)["pending_notes"]:
        pass
    return paths


def view(config, scope):
    with database.connect(config.state_root) as db:
        row = auth.grant(db, scope[0], scope[1], "query")
        return indexed_sources.read(config, db, scope[0], row)


def test_real_former_proof_preserves_sixty_notes_before_and_during_every_upgrade_slice(
    config, scope, package, monkeypatch
):
    client, vault, root = scope
    with database.connect(config.state_root) as db:
        packages.commit(
            config,
            db,
            client,
            vault,
            packages.inspect(config, db, client, vault, "Book/fake-thermal"),
            notify=False,
        )
    paths = corpus(config, scope)
    old = install_former_proof(config, scope)
    before_bytes = {path: (root / path).read_bytes() for path in paths}

    def verify():
        assert set(paths) <= view(config, scope).notes.keys()
        with database.connect(config.state_root) as db:
            for path, term in paths.items():
                found = search.search(config, db, client, term, [vault])
                assert any(row["note_path"] == path for row in found["results"])

    verify()
    # One genuinely changed path binding must be re-admitted, while all sixty other
    # notes remain queryable. The former algorithm kept this outside its context.
    with database.connect(config.state_root) as db:
        writer = auth.create_client(db, "legacy tracking test", False)
        auth.set_grant(db, writer["id"], vault, query=True, original=False, add=True)
    note_identities.run(
        config,
        "Bearer " + writer["token"],
        vault,
        "track",
        "tracked.md",
        digest((root / "tracked.md").read_bytes()),
    )
    current = view(config, scope)
    assert set(paths) <= current.notes.keys() and "tracked.md" not in current.notes
    for n in range(13):
        (root / f"Corpus/new-{n:02}.md").write_text("new unrelated content")
    store.enqueue(config, vault, [("Corpus", 1)], "17", now=20)
    while tasks := store.due(config, now=30):
        for task in tasks:
            scanner.apply(config, task, now=30)
    monkeypatch.setattr(index, "BATCH", 7)
    count = 0
    while True:
        result = build(config, scope)
        count += 1
        verify()
        with note_search_cache.connection(config, vault) as db:
            published = visibility.previous_readonly(db)
            assert published.context != old.context
        if not result["pending_notes"]:
            break
    assert count == 2
    assert all((root / path).read_bytes() == raw for path, raw in before_bytes.items())


@pytest.mark.parametrize(
    "change",
    ["scope", "package", "root_mode", "missing_root", "missing_proof", "metadata", "generation"],
)
def test_former_proof_never_bridges_changed_or_unverifiable_authority(
    config, scope, package, monkeypatch, change
):
    client, vault, root = scope
    paths = corpus(config, scope)
    install_former_proof(config, scope)
    if change == "scope":
        with database.connect(config.state_root) as db:
            db.execute(
                "UPDATE vaults SET scope_json=? WHERE id=?",
                ('{"include":[""],"exclude":["Corpus/u-00.md"]}', vault),
            )
        store.enqueue(config, vault, [("", 1)], "17")
        while tasks := store.due(config, now=time.time() + 100):
            for task in tasks:
                scanner.apply(config, task)
    elif change == "package":
        with database.connect(config.state_root) as db:
            packages.commit(
                config,
                db,
                client,
                vault,
                packages.inspect(config, db, client, vault, "Book/fake-thermal"),
                notify=False,
            )
    elif change == "root_mode":
        root.chmod((root.stat().st_mode & 0o777) ^ 0o040)
    elif change == "missing_root":
        with store.connection(config, write=True) as db:
            db.execute("DELETE FROM revisions WHERE vault=? AND path=''", (vault,))
    else:
        with sqlite3.connect(note_search_cache.location(config, vault)) as db:
            if change == "missing_proof":
                db.execute("DELETE FROM sync_projection")
            elif change == "metadata":
                db.execute("UPDATE sync_metadata SET visibility='unverified'")
            else:
                db.execute("UPDATE generation SET fingerprint='unverified'")
    assert not (set(paths) & view(config, scope).notes.keys())
    monkeypatch.setattr(index, "BATCH", 7)
    assert build(config, scope)["pending_notes"]
    admitted = set(paths) & view(config, scope).notes.keys()
    assert 0 < len(admitted) <= 7
    if change == "scope":
        assert "Corpus/u-00.md" not in admitted
