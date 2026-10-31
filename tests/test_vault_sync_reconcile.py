"""Working edits preserve stable identities and original bytes; ambiguous copies stay blocked."""

import os
from unittest.mock import patch

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_identities import track

from fiber_bundle import auth, database, note_identity_store, vault_scope
from fiber_bundle.config import IntegrityError
from fiber_bundle.vault_sync import reconcile, scanner, store


def observe(config, identity_scope):
    _, vid, root = identity_scope
    store.register(config, vid, str(root), vault_scope.Scope(), "test")
    while tasks := store.due(config, now=10):
        for task in tasks:
            scanner.apply(config, task, now=10)


def changed(config, vid, path="manual.md", flags=2):
    store.enqueue(config, vid, [(path, flags)], "20", now=20)
    while tasks := store.due(config, now=30):
        for task in tasks:
            scanner.apply(config, task, now=30)


def test_edit_retains_identity_and_history_without_rewriting_body(config, identity_scope):
    client, vid, root = identity_scope
    initial = track(config, identity_scope)
    observe(config, identity_scope)
    note = root / "manual.md"
    note.write_text("# My edits\nnew fact\n")
    changed(config, vid)
    before = note.read_bytes(), note.stat().st_mtime_ns
    result = reconcile.current(config, client["id"], vid, "manual.md")
    assert result.identity == initial["note_identity"]
    assert (note.read_bytes(), note.stat().st_mtime_ns) == before
    records = note_identity_store.records(config, vid)
    assert records[0].revision == 2 and records[0].sha256 == result.sha256
    reconcile.current(config, client["id"], vid, "manual.md")
    assert note_identity_store.records(config, vid) == records
    with note_identity_store.connection(config) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 2


def test_observed_atomic_save_keeps_note_id(config, identity_scope):
    client, vid, root = identity_scope
    initial = track(config, identity_scope)
    observe(config, identity_scope)
    temp = root / ".save-temp"
    temp.write_text("# Saved replacement\nchanged content\n")
    os.replace(temp, root / "manual.md")
    changed(config, vid, flags=4 | 2)
    result = reconcile.current(config, client["id"], vid, "manual.md")
    assert result.identity == initial["note_identity"]
    assert note_identity_store.records(config, vid)[0].file_key == result.file_key


@pytest.mark.parametrize("flags", [0, 1, 4 | 8])
def test_replacement_without_continuous_save_evidence_requires_review(
    config, identity_scope, flags
):
    client, vid, root = identity_scope
    track(config, identity_scope)
    observe(config, identity_scope)
    old = note_identity_store.records(config, vid)
    temp = root / ".save-temp"
    temp.write_text("unrelated document")
    os.replace(temp, root / "manual.md")
    changed(config, vid, flags=flags)
    with pytest.raises(IntegrityError, match="replacement_needs_review"):
        reconcile.current(config, client["id"], vid, "manual.md")
    assert note_identity_store.records(config, vid) == old


def test_original_file_still_present_is_not_adopted(config, identity_scope):
    client, vid, root = identity_scope
    track(config, identity_scope)
    observe(config, identity_scope)
    (root / "manual.md").rename(root / "original.md")
    (root / "manual.md").write_text("new unrelated file")
    changed(config, vid, path="original.md", flags=4)
    changed(config, vid, flags=4)
    with pytest.raises(IntegrityError, match="replacement_needs_review"):
        reconcile.current(config, client["id"], vid, "manual.md")


def test_revocation_still_blocks_before_body_read(config, identity_scope):
    client, vid, root = identity_scope
    observe(config, identity_scope)
    with database.connect(config.state_root) as db:
        auth.set_grant(db, client["id"], vid, query=False, original=False, add=True)
    with patch.object(
        reconcile.note_index, "read_note", side_effect=AssertionError("unauthorized read")
    ):
        with pytest.raises(auth.Denied):
            reconcile.current(config, client["id"], vid, "manual.md")


def test_managed_article_revision_keeps_original_hashes_and_provenance(
    config, policy, article_scope
):
    from test_note_placement import published

    from fiber_bundle import note_inventory, note_provenance, publication_locations

    _, result, note = published(config, policy, article_scope)
    client, vid = article_scope[:2]
    root = note.parent.parent
    relative = note.relative_to(root).as_posix()
    observe(config, ({"id": client}, vid, root))
    with database.connect(config.state_root) as db:
        original = publication_locations.get(db, "article", result["publication_id"])
    temporary = note.with_suffix(".tmp")
    temporary.write_text(note.read_text() + "\nmanual revision sentinel\n")
    temporary.replace(note)
    changed(config, vid, path=relative, flags=4 | 2)
    body = note.read_bytes()
    reconcile.current(config, client, vid, relative)
    with database.connect(config.state_root) as db:
        current = publication_locations.get(db, "article", result["publication_id"])
        assert current["plan"]["published_hashes"] == original["plan"]["published_hashes"]
        assert current["plan"]["outputs"] == original["plan"]["outputs"]
        inventory = note_inventory.read(config, db, auth.grant(db, client, vid, "query"))
        edited = next(n for n in inventory.notes if n.relative_path == relative)
        provenance = note_provenance.describe(
            config, db, client, auth.grant(db, client, vid, "query"), inventory, edited
        )
        assert provenance["source_kind"] == "user_revision" and provenance["originals"]
    assert note.read_bytes() == body


def test_late_registration_of_same_observed_file_is_not_a_conflict(config, identity_scope):
    client, vid, root = identity_scope
    observe(config, identity_scope)
    reconcile.current(config, client["id"], vid, "manual.md")
    with store.connection(config) as db:
        frozen = db.execute("SELECT value FROM working_versions").fetchone()[0]
    body = (root / "manual.md").read_bytes()
    initial = track(config, identity_scope)
    result = reconcile.current(config, client["id"], vid, "manual.md")
    assert result.identity == initial["note_identity"]
    assert (root / "manual.md").read_bytes() == body
    with store.connection(config) as db:
        assert db.execute("SELECT value FROM working_versions").fetchone()[0] == frozen
    assert note_identity_store.records(config, vid)[0].revision == 1


def test_late_registration_with_wrong_content_evidence_stays_blocked(config, identity_scope):
    from dataclasses import replace

    client, vid, _ = identity_scope
    observe(config, identity_scope)
    reconcile.current(config, client["id"], vid, "manual.md")
    track(config, identity_scope)
    original = note_identity_store.records(config, vid)[0]
    bad = replace(original, sha256="0" * 64)
    with patch.object(note_identity_store, "records", return_value=[bad]):
        with pytest.raises(IntegrityError, match="identity_conflict"):
            reconcile.current(config, client["id"], vid, "manual.md")


def test_published_note_observed_before_registration_keeps_publication_and_originals(
    config, policy, article_scope
):
    from test_note_placement import published

    from fiber_bundle import note_index, publication_locations

    _, result, note = published(config, policy, article_scope)
    client, vid = article_scope[:2]
    root = note.parent.parent
    relative = note.relative_to(root).as_posix()
    observe(config, ({"id": client}, vid, root))
    first = reconcile.current(config, client, vid, relative)
    with database.connect(config.state_root) as db:
        original = publication_locations.get(db, "article", result["publication_id"])
    info = root.stat()
    registration = note_identity_store.Registration(
        first.identity,
        first.publication_id,
        relative,
        first.sha256,
        first.file_key,
        (info.st_dev, info.st_ino),
        1,
    )
    note_identity_store.save(config, vid, client, registration, expected_revision=None)
    assert reconcile.current(config, client, vid, relative) == first
    with database.connect(config.state_root) as db:
        assert publication_locations.get(db, "article", result["publication_id"]) == original
    assert note_index.read_note(root, relative, 1024 * 1024).sha256 == first.sha256
