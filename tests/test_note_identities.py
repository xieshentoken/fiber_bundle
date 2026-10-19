import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_changes import scope as scope
from test_note_moves import published as published

from fiber_bundle import (
    auth,
    database,
    note_identities,
    note_identity_store,
    note_index,
    note_snapshots,
    search,
)
from fiber_bundle.config import IntegrityError, StorageError


@pytest.fixture
def identity_scope(config, scope):
    db = database.connect(config.state_root)
    auth.set_grant(db, scope[0]["id"], scope[1], query=True, original=False, add=True)
    db.close()
    return scope


def track(config, scope, path="manual.md", *, action="track", **extra):
    note = scope[2] / path
    return note_identities.run(
        config,
        "Bearer " + scope[0]["token"],
        scope[1],
        action,
        path,
        hashlib.sha256(note.read_bytes()).hexdigest(),
        **extra,
    )


def hits(config, scope, query="sentinel"):
    db = database.connect(config.state_root)
    try:
        return search.search(config, db, scope[0]["id"], query, [scope[1]])["results"]
    finally:
        db.close()


def ledger(config):
    with note_identity_store.connection(config) as db:
        assert db is not None
        return [row[:] for row in db.execute("SELECT * FROM registrations ORDER BY identity")], [
            row[:] for row in db.execute("SELECT * FROM events ORDER BY id")
        ]


def test_opt_in_registration_preserves_body_attributes_and_is_idempotent(config, identity_scope):
    note = identity_scope[2] / "manual.md"
    before = note.read_bytes(), note.stat().st_mtime_ns, note.stat().st_ino
    assert "note_id" not in hits(config, identity_scope)[0]
    assert not (config.state_root / "note-identities.sqlite3").exists()
    registered = track(config, identity_scope)
    assert registered["note_identity"].startswith("note:") and registered["revision"] == 1
    assert track(config, identity_scope) == registered
    assert (note.read_bytes(), note.stat().st_mtime_ns, note.stat().st_ino) == before
    assert hits(config, identity_scope)[0]["note_id"] == registered["note_identity"]
    assert len(ledger(config)[1]) == 1
    assert stat.S_IMODE((config.state_root / "note-identities.sqlite3").stat().st_mode) == 0o600


def test_historical_moves_edits_and_snapshot_conflicts_keep_id(config, identity_scope):
    registered = track(config, identity_scope)
    client, vid, root = identity_scope
    baseline = note_snapshots.run(config, "Bearer " + client["token"], vid, "capture")[
        "snapshot_id"
    ]
    (root / "folder").mkdir()
    (root / "manual.md").rename(root / "folder/renamed.md")
    (root / "folder/renamed.md").write_text("# 人工属性保留\nnew-sentinel\n")
    before = ledger(config)
    result = hits(config, identity_scope, "new-sentinel")[0]
    assert result["note_id"] == registered["note_identity"]
    assert result["note_path"] == "folder/renamed.md"
    assert result["source_kind"] == "historical_note" and result["source_version_id"] is None
    changes = note_snapshots.run(
        config, "Bearer " + client["token"], vid, "compare", snapshot_id=baseline
    )
    assert len(changes["changes"]) == 1 and changes["changes"][0]["kind"] == "moved_and_edited"
    assert changes["changes"][0]["identity"] == registered["note_identity"]
    assert ledger(config) == before


def test_equal_content_copies_remain_separate_and_are_not_adopted(config, identity_scope):
    first = track(config, identity_scope)
    shutil.copy2(identity_scope[2] / "manual.md", identity_scope[2] / "copy.md")
    found = {item["note_path"]: item for item in hits(config, identity_scope)}
    assert found["manual.md"]["note_id"] == first["note_identity"]
    assert "note_id" not in found["copy.md"]
    second = track(config, identity_scope, "copy.md")
    assert second["note_identity"] != first["note_identity"]
    assert len(ledger(config)[0]) == 2


def test_atomic_replacement_requires_explicit_hash_revision_and_keeps_old_file(
    config, identity_scope
):
    registered = track(config, identity_scope)
    note = identity_scope[2] / "manual.md"
    preserved = config.project_root / "preserved-old.md"
    note.rename(preserved)
    note.write_text("new-sentinel")
    with pytest.raises(IntegrityError, match="replaced"):
        hits(config, identity_scope)
    before = ledger(config)
    with pytest.raises(IntegrityError, match="revision"):
        track(
            config,
            identity_scope,
            action="rebind",
            identity=registered["note_identity"],
            revision=2,
        )
    with pytest.raises(IntegrityError, match="hash conflict"):
        note_identities.run(
            config,
            "Bearer " + identity_scope[0]["token"],
            identity_scope[1],
            "rebind",
            "manual.md",
            "0" * 64,
            identity=registered["note_identity"],
            revision=1,
        )
    assert ledger(config) == before
    rebound = track(
        config, identity_scope, action="rebind", identity=registered["note_identity"], revision=1
    )
    assert rebound["revision"] == 2 and rebound["note_identity"] == registered["note_identity"]
    assert hits(config, identity_scope, "new-sentinel")[0]["note_id"] == registered["note_identity"]
    assert "private-body-sentinel" in preserved.read_text() and note.read_text() == "new-sentinel"
    assert len(ledger(config)[1]) == 2


def test_visible_original_and_already_registered_replacement_block_rebind(config, identity_scope):
    first = track(config, identity_scope)
    (identity_scope[2] / "copy.md").write_bytes((identity_scope[2] / "manual.md").read_bytes())
    before = ledger(config)
    with pytest.raises(IntegrityError, match="still visible"):
        track(
            config,
            identity_scope,
            "copy.md",
            action="rebind",
            identity=first["note_identity"],
            revision=1,
        )
    second = track(config, identity_scope, "copy.md")
    (identity_scope[2] / "manual.md").unlink()
    with pytest.raises(IntegrityError, match="another identity"):
        track(
            config,
            identity_scope,
            "copy.md",
            action="rebind",
            identity=first["note_identity"],
            revision=1,
        )
    assert len(ledger(config)[1]) == len(before[1]) + 1 and second["revision"] == 1


def test_deleted_note_stays_missing_and_other_vault_does_not_inherit_id(config, identity_scope):
    registered = track(config, identity_scope)
    note = identity_scope[2] / "manual.md"
    other = config.project_root / "test vault/temp_vault2"
    note.rename(other / "moved.md")
    assert hits(config, identity_scope) == [] and not note.exists()
    db = database.connect(config.state_root)
    vid = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
    auth.set_grant(db, identity_scope[0]["id"], vid, query=True, original=False, add=False)
    result = search.search(config, db, identity_scope[0]["id"], "sentinel", [vid])["results"][0]
    db.close()
    assert "note_id" not in result
    assert ledger(config)[0][0][1] == registered["note_identity"]


def test_deleted_identity_does_not_attach_to_recreated_same_bytes(config, identity_scope):
    registered = track(config, identity_scope)
    path = identity_scope[2] / "manual.md"
    data = path.read_bytes()
    path.rename(config.project_root / "kept-original")
    path.write_bytes(data)
    with pytest.raises(IntegrityError, match="replaced"):
        hits(config, identity_scope)
    assert ledger(config)[0][0][1] == registered["note_identity"]


def test_identity_record_scope_limit_and_vault_replacement(config, identity_scope):
    track(config, identity_scope)
    (identity_scope[2] / "second.md").write_text("second")
    before = ledger(config)
    with patch.object(note_identity_store, "MAX_REGISTRATIONS", 1):
        with pytest.raises(IntegrityError, match="limit"):
            track(config, identity_scope, "second.md")
    assert ledger(config) == before
    identity_scope[2].rename(identity_scope[2].with_name("old-vault"))
    identity_scope[2].mkdir()
    with pytest.raises(IntegrityError, match="vault changed"):
        hits(config, identity_scope)


def test_revoke_during_registration_prevents_sidecar_commit(config, identity_scope):
    read = note_index.read_note
    calls = 0

    def revoke(*args):
        nonlocal calls
        calls += 1
        note = read(*args)
        if calls == 2:
            db = database.connect(config.state_root)
            auth.set_grant(
                db,
                identity_scope[0]["id"],
                identity_scope[1],
                query=True,
                original=False,
                add=False,
            )
            db.close()
        return note

    with patch.object(note_index, "read_note", side_effect=revoke):
        with pytest.raises(auth.Denied):
            track(config, identity_scope)
    assert not (config.state_root / "note-identities.sqlite3").exists()


def test_identity_change_before_search_delivery_refuses_same_bytes(config, identity_scope):
    track(config, identity_scope)
    read = note_index.read_note
    calls = 0

    def replace_before_delivery(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            path = identity_scope[2] / "manual.md"
            data = path.read_bytes()
            path.rename(config.project_root / "preserved-before-delivery")
            path.write_bytes(data)
        return read(*args)

    with patch.object(note_index, "read_note", side_effect=replace_before_delivery):
        with pytest.raises(IntegrityError, match="identity changed before delivery"):
            hits(config, identity_scope)


@pytest.mark.parametrize("query,add", [(False, False), (False, True), (True, False)])
def test_registration_requires_both_capabilities_before_reads(config, identity_scope, query, add):
    db = database.connect(config.state_root)
    auth.set_grant(
        db, identity_scope[0]["id"], identity_scope[1], query=query, original=True, add=add
    )
    db.close()
    with patch.object(
        note_identity_store, "records", side_effect=AssertionError("must authorize first")
    ):
        with pytest.raises(auth.Denied):
            track(config, identity_scope)
    assert not (config.state_root / "note-identities.sqlite3").exists()


def test_query_peer_can_read_shared_identity_but_cannot_register(config, identity_scope):
    registered = track(config, identity_scope)
    db = database.connect(config.state_root)
    peer = auth.create_client(db, "query-peer", False)
    auth.set_grant(db, peer["id"], identity_scope[1], query=True, original=False, add=False)
    result = search.search(config, db, peer["id"], "sentinel", [identity_scope[1]])["results"][0]
    db.close()
    assert result["note_id"] == registered["note_identity"]
    with pytest.raises(auth.Denied):
        track(config, (peer, identity_scope[1], identity_scope[2]))


@pytest.mark.parametrize(
    "path", ["../outside.md", "/tmp/outside.md", ".hidden.md", "a//b.md", "manual.txt"]
)
def test_invalid_registration_paths_refuse(config, identity_scope, path):
    with pytest.raises(StorageError):
        note_identities.run(
            config,
            "Bearer " + identity_scope[0]["token"],
            identity_scope[1],
            "track",
            path,
            "a" * 64,
        )
    assert not (config.state_root / "note-identities.sqlite3").exists()


def test_missing_birth_and_changed_before_commit_refuse(config, identity_scope):
    read = note_index.read_note
    with patch.object(
        note_index, "read_note", side_effect=lambda *a: replace(read(*a), file_key=None)
    ):
        with pytest.raises(IntegrityError, match="birth unavailable"):
            track(config, identity_scope)
    assert not (config.state_root / "note-identities.sqlite3").exists()
    calls = 0

    def changed(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            (identity_scope[2] / "manual.md").write_text("changed")
        return read(*args)

    with patch.object(note_index, "read_note", side_effect=changed):
        with pytest.raises(IntegrityError, match="before registration"):
            track(config, identity_scope)
    assert not (config.state_root / "note-identities.sqlite3").exists()


@pytest.mark.parametrize("mutation", ["symlink", "hardlink", "fifo", "schema", "birth"])
def test_invalid_identity_ledger_fails_closed(config, identity_scope, mutation):
    track(config, identity_scope)
    path = config.state_root / "note-identities.sqlite3"
    if mutation in {"schema", "birth"}:
        db = sqlite3.connect(path)
        db.execute(
            "PRAGMA user_version=9"
            if mutation == "schema"
            else "UPDATE registrations SET file_birth='invalid'"
        )
        db.commit()
        db.close()
    else:
        preserved = config.project_root / "ledger-copy"
        path.rename(preserved)
        if mutation == "symlink":
            path.symlink_to(preserved)
        elif mutation == "hardlink":
            os.link(preserved, path)
        else:
            os.mkfifo(path)
    with pytest.raises(StorageError):
        hits(config, identity_scope)


@pytest.mark.parametrize("edited", [False, True])
def test_tracked_article_single_filename_and_directory_rename_keep_provenance(
    config, article_scope, published, edited
):
    db, publication, note, _ = published
    client = auth.create_client(db, "article-tracker", False)
    auth.set_grant(db, client["id"], article_scope[1], query=True, original=True, add=True)
    scoped = (client, article_scope[1], note.parent.parent)
    original = search.search(config, db, client["id"], "Synthetic", [article_scope[1]])["results"][
        0
    ]
    registered = track(config, scoped, publication["note_path"])
    assert registered["note_identity"] == "publication:" + publication["publication_id"]
    renamed = note.with_name("renamed.MD")
    note.rename(renamed)
    if edited:
        renamed.write_text("# Synthetic\nhuman-sentinel\n")
    target = note.parent.parent / "directory-renamed"
    note.parent.rename(target)
    current = search.search(config, db, client["id"], "Synthetic", [article_scope[1]])["results"][0]
    assert current["note_id"] == registered["note_identity"]
    assert current["note_path"] == "directory-renamed/renamed.MD"
    assert current["source_version_id"] == original["source_version_id"]
    assert current["originals"] == original["originals"]
    assert current["source_kind"] == ("user_revision" if edited else "published_article")
    assert not note.exists()


def test_tracked_publication_rebind_and_visible_copy_boundary(config, article_scope, published):
    db, publication, note, _ = published
    client = auth.create_client(db, "article-tracker", False)
    auth.set_grant(db, client["id"], article_scope[1], query=True, original=False, add=True)
    scoped = (client, article_scope[1], note.parent.parent)
    registered = track(config, scoped, publication["note_path"])
    renamed = note.with_name("renamed.md")
    note.rename(renamed)
    copy = note.with_name("copy.md")
    copy.write_bytes(renamed.read_bytes())
    with pytest.raises(IntegrityError, match="still exists"):
        track(
            config,
            scoped,
            copy.relative_to(scoped[2]).as_posix(),
            action="rebind",
            identity=registered["note_identity"],
            revision=1,
        )
    renamed.rename(config.project_root / "preserved-article.md")
    rebound = track(
        config,
        scoped,
        copy.relative_to(scoped[2]).as_posix(),
        action="rebind",
        identity=registered["note_identity"],
        revision=1,
    )
    assert rebound["revision"] == 2
    found = search.search(config, db, client["id"], "Synthetic", [article_scope[1]])["results"]
    assert found[0]["note_path"].endswith("/copy.md")
    copy.unlink()
    assert search.search(config, db, client["id"], "Synthetic", [article_scope[1]])["results"] == []


def test_real_cli_track_rename_and_rebind(config, identity_scope):
    token = config.project_root / "client-token"
    token.write_text(identity_scope[0]["token"])
    flags = [
        "--project-root",
        str(config.project_root),
        "--config",
        "config.json",
        "--token-file",
        str(token),
        "--vault-id",
        identity_scope[1],
    ]
    env = {**os.environ, "PYTHONPATH": str(Path(note_identities.__file__).parent.parent)}

    def call(action, path, **options):
        digest = hashlib.sha256((identity_scope[2] / path).read_bytes()).hexdigest()
        args = [
            sys.executable,
            "-m",
            "fiber_bundle.note_changes_cli",
            action,
            *flags,
            "--note-path",
            path,
            "--expected-sha256",
            digest,
        ]
        for key, value in options.items():
            args.extend(["--" + key.replace("_", "-"), str(value)])
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=10)

    result = call("track", "manual.md")
    assert result.returncode == 0
    registration = json.loads(result.stdout)
    (identity_scope[2] / "manual.md").rename(identity_scope[2] / "renamed.md")
    assert hits(config, identity_scope)[0]["note_id"] == registration["note_identity"]
    (identity_scope[2] / "renamed.md").rename(config.project_root / "old-copy")
    (identity_scope[2] / "renamed.md").write_text("replacement-sentinel")
    result = call("rebind", "renamed.md", note_identity=registration["note_identity"], revision=1)
    assert result.returncode == 0 and json.loads(result.stdout)["revision"] == 2
    failed = call("rebind", "renamed.md", note_identity=registration["note_identity"], revision=1)
    assert (
        failed.returncode == 1
        and json.loads(failed.stdout)["error_code"] == "note_identity_rejected"
    )
    assert identity_scope[0]["token"] not in failed.stdout
