"""Persistent coalescing and real isolated filesystem observations; never a live Vault."""

from unittest.mock import patch

from fiber_bundle import vault_scope
from fiber_bundle.vault_sync import scanner, store


def setup_library(config):
    config.state_root.mkdir()
    root = config.project_root / "vault"
    root.mkdir()
    store.register(config, "v1", str(root), vault_scope.Scope(exclude=("protected",)), "binding")
    return root


def drain(config, now=10):
    while tasks := store.due(config, now=now):
        for task in tasks:
            scanner.apply(config, task, now=now)


def test_initial_catalogue_and_idle_reads_never_open_bodies_or_rescan(config):
    root = setup_library(config)
    (root / "one.md").write_text("initial")
    (root / "sub").mkdir()
    (root / "sub/two.md").write_text("nested")
    (root / "protected").mkdir()
    (root / "protected/secret.md").write_text("excluded")
    drain(config)
    first = store.status(config, "v1")
    assert first["baseline"] == 1 and first["scans"] == 2
    with patch("os.scandir", side_effect=AssertionError("idle scan")):
        for now in range(11, 1811):
            assert store.due(config, now=now) == []
            assert store.status(config, "v1")["bodies"] == 0
            scanner.fingerprint(config, "v1")
    with store.connection(config) as db:
        assert not any("protected" in r[0] for r in db.execute("SELECT path FROM files"))


def test_repeated_saves_are_coalesced_and_cursor_is_durable(config):
    root = setup_library(config)
    path = root / "note.md"
    path.write_text("first")
    drain(config)
    path.write_text("second")
    for i in range(10):
        store.enqueue(config, "v1", [("note.md", 0)], str(100 + i), now=20 + i)
    assert store.due(config, now=33) == []
    assert len(store.due(config, now=34)) == 1
    scans = store.status(config, "v1")["scans"]
    drain(config, 34)
    assert store.status(config, "v1")["scans"] == scans
    assert store.library(config, "v1")["cursor"] == "109"
    store.register(config, "v1", str(root), vault_scope.Scope(exclude=("protected",)), "binding")
    assert store.due(config, now=100) == []


def test_error_backoff_survives_new_events_and_restart(config):
    root = setup_library(config)
    drain(config)
    store.enqueue(config, "v1", [("missing.md", 0)], "5", now=20)
    task = store.due(config, now=25)[0]
    store.failed(config, task, "waiting", now=25)
    store.enqueue(config, "v1", [("missing.md", 0)], "6", now=26)
    store.register(config, "v1", str(root), vault_scope.Scope(exclude=("protected",)), "binding")
    assert store.due(config, now=84) == []
    task = store.due(config, now=85)[0]
    assert task["attempts"] == 1
    store.failed(config, task, "waiting", now=85)
    assert store.due(config, now=384) == []
    assert len(store.due(config, now=385)) == 1


def test_protected_and_hidden_events_do_not_enter_ledger(config):
    setup_library(config)
    drain(config)
    before = store.library(config, "v1")["generation"]
    store.enqueue(
        config, "v1", [("protected/secret", 0), (".obsidian/workspace.json", 0)], "12", now=20
    )
    assert store.library(config, "v1")["generation"] == before
    assert store.due(config, now=100) == []


def test_deleted_subdirectory_removes_only_its_catalogue(config):
    root = setup_library(config)
    (root / "sub").mkdir()
    (root / "sub/n.md").write_text("one")
    (root / "keep.md").write_text("two")
    drain(config)
    (root / "sub/n.md").unlink()
    (root / "sub").rmdir()
    store.enqueue(config, "v1", [("sub", 0)], "9", now=20)
    drain(config, 25)
    with store.connection(config) as db:
        names = [r[0] for r in db.execute("SELECT path FROM files")]
    assert names == ["", "keep.md"]


def test_large_directory_is_split_and_continuation_survives_reopen(config):
    root = setup_library(config)
    for i in range(250):
        (root / f"{i:03}.md").write_text("x")
    scanner.apply(config, store.due(config, now=1)[0], now=1)
    assert store.status(config, "v1")["stats"] == 101
    assert store.status(config, "v1")["baseline"] == 0
    scanner.apply(config, store.due(config, now=2)[0], now=2)
    scanner.apply(config, store.due(config, now=3)[0], now=3)
    value = store.status(config, "v1")
    assert value["baseline"] == 1 and value["scans"] == 1
    assert value["stats"] == 253


def test_traversal_event_rejected_without_filesystem_access(config):
    setup_library(config)
    drain(config)
    store.enqueue(config, "v1", [("../outside", 0), ("/absolute", 0)], "16", now=20)
    assert store.due(config, now=30) == []
