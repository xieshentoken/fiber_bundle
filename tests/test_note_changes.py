import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_moves import published as published

from fiber_bundle import auth, database, note_changes, note_inventory, note_snapshots
from fiber_bundle.config import IntegrityError, InvalidInput, StorageError
from fiber_bundle.inputs import canonical


@pytest.fixture
def scope(config, store):
    db = database.connect(config.state_root)
    client = auth.create_client(db, "snapshot-owner", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vid, query=True, original=False, add=False)
    db.close()
    root = config.project_root / "test vault/temp_vault1"
    (root / "manual.md").write_text("# 人工正文\nprivate-body-sentinel\n")
    return client, vid, root


def run(config, scope, action="capture", **kwargs):
    client, vid, _ = scope
    return note_snapshots.run(config, "Bearer " + client["token"], vid, action, **kwargs)


def stored(config, scope, identity):
    return note_snapshots.path_for(config, scope[0]["id"], scope[1], identity)


def test_explicit_capture_is_immutable_metadata_only_and_idempotent(config, scope):
    note = scope[2] / "manual.md"
    before = note.read_bytes(), note.stat().st_mtime_ns
    result = run(config, scope)
    assert result["note_count"] == 1 and result["metadata_only"] is True
    path = stored(config, scope, result["snapshot_id"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = path.read_bytes()
    assert b"private-body-sentinel" not in raw and scope[0]["token"].encode() not in raw
    assert str(scope[2]).encode() not in raw
    assert run(config, scope) == result
    assert (note.read_bytes(), note.stat().st_mtime_ns) == before
    assert run(config, scope, "compare", snapshot_id=result["snapshot_id"])["status"] == "unchanged"
    assert (
        run(
            config,
            scope,
            "check",
            snapshot_id=result["snapshot_id"],
            note_identity="path:manual.md",
        )["status"]
        == "match"
    )


def test_edit_add_missing_report_and_hash_conflict_preserve_files(config, scope):
    baseline = run(config, scope)["snapshot_id"]
    path = stored(config, scope, baseline)
    saved = path.read_bytes()
    note = scope[2] / "manual.md"
    note.write_text("user-edit-sentinel")
    (scope[2] / "unclassified.md").write_text("new content")
    changes = run(config, scope, "compare", snapshot_id=baseline)
    assert {entry["identity"]: entry["kind"] for entry in changes["changes"]} == {
        "path:manual.md": "edited",
        "path:unclassified.md": "added",
    }
    checked = run(config, scope, "check", snapshot_id=baseline, note_identity="path:manual.md")
    assert checked["status"] == "conflict" and checked["reason"] == "edited"
    assert checked["expected"]["sha256"] != checked["current"]["sha256"]
    assert checked["write_performed"] is False and checked["write_authorization"] is False
    assert path.read_bytes() == saved and note.read_text() == "user-edit-sentinel"
    note.unlink()
    checked = run(config, scope, "check", snapshot_id=baseline, note_identity="path:manual.md")
    assert checked["reason"] == "missing" and checked["current"] is None
    assert not note.exists() and path.read_bytes() == saved


def test_historical_rename_is_not_guessed_from_identical_bytes(config, scope):
    baseline = run(config, scope)["snapshot_id"]
    (scope[2] / "manual.md").rename(scope[2] / "renamed.md")
    changes = run(config, scope, "compare", snapshot_id=baseline)["changes"]
    assert {item["kind"] for item in changes} == {"added", "missing"}


@pytest.mark.parametrize("edited", [False, True])
def test_published_move_and_combined_edit_keep_identity(config, article_scope, published, edited):
    db, result, note, _ = published
    cid, vid, _ = article_scope
    baseline = note_snapshots.capture_locked(config, db, cid, vid)["snapshot_id"]
    target = note.parent.parent / "moved"
    note.parent.rename(target)
    if edited:
        (target / "note.md").write_text("human changed the note")
    current = note_snapshots.compare_locked(config, db, cid, vid, baseline)
    assert len(current["changes"]) == 1
    change = current["changes"][0]
    assert change["identity"] == "publication:" + result["publication_id"]
    assert change["kind"] == ("moved_and_edited" if edited else "moved")
    checked = note_snapshots.compare_locked(
        config, db, cid, vid, baseline, note_identity=change["identity"]
    )
    assert checked["status"] == "conflict"
    assert checked["current"]["note_path"] == "moved/note.md"


@pytest.mark.parametrize("action", ["capture", "compare", "check"])
def test_denied_scope_never_scans_or_reads_snapshot(config, scope, action):
    baseline = run(config, scope)["snapshot_id"]
    db = database.connect(config.state_root)
    auth.set_grant(db, scope[0]["id"], scope[1], query=False, original=True, add=True)
    db.close()
    extra = {} if action == "capture" else {"snapshot_id": baseline}
    if action == "check":
        extra["note_identity"] = "path:manual.md"
    with (
        patch.object(note_inventory, "read", side_effect=AssertionError("must authorize first")),
        patch.object(
            note_snapshots, "read_bounded", side_effect=AssertionError("must authorize first")
        ),
    ):
        with pytest.raises(auth.Denied):
            run(config, scope, action, **extra)


def test_other_client_cannot_reuse_snapshot_even_with_query_grant(config, scope):
    baseline = run(config, scope)["snapshot_id"]
    db = database.connect(config.state_root)
    client = auth.create_client(db, "other", False)
    auth.set_grant(db, client["id"], scope[1], query=True, original=False, add=False)
    db.close()
    with pytest.raises(IntegrityError):
        run(config, (client, scope[1], scope[2]), "compare", snapshot_id=baseline)
    copied = stored(config, (client, scope[1], scope[2]), baseline)
    copied.parent.mkdir(parents=True)
    copied.write_bytes(stored(config, scope, baseline).read_bytes())
    with pytest.raises(IntegrityError, match="scope or vault identity"):
        run(config, (client, scope[1], scope[2]), "compare", snapshot_id=baseline)


@pytest.mark.parametrize("mode", ["tamper", "hardlink", "symlink", "oversize", "fifo"])
def test_unsafe_or_changed_baseline_is_refused(config, scope, mode):
    baseline = run(config, scope)["snapshot_id"]
    path = stored(config, scope, baseline)
    if mode == "tamper":
        path.write_text("{}")
    elif mode == "oversize":
        path.write_bytes(b"x" * (note_changes.MAX_BYTES + 1))
    else:
        other = config.project_root / "outside-snapshot"
        path.rename(other)
        if mode == "hardlink":
            os.link(other, path)
        elif mode == "symlink":
            path.symlink_to(other)
        else:
            os.mkfifo(path)
    with pytest.raises(StorageError):
        run(config, scope, "compare", snapshot_id=baseline)


def test_two_sample_race_or_revocation_does_not_write_snapshot(config, scope):
    read = note_inventory.read
    calls = 0

    def changing(*args):
        nonlocal calls
        calls += 1
        result = read(*args)
        (scope[2] / "manual.md").write_text(str(calls))
        return result

    with patch.object(note_inventory, "read", side_effect=changing):
        with pytest.raises(IntegrityError, match="changed during"):
            run(config, scope)
    assert not (config.state_root / "note-snapshots").exists()

    def revoke(config, db, vault):
        result = read(config, db, vault)
        auth.set_grant(db, scope[0]["id"], scope[1], query=False, original=False, add=False)
        return result

    with patch.object(note_inventory, "read", side_effect=revoke):
        with pytest.raises(auth.Denied):
            run(config, scope)
    assert not (config.state_root / "note-snapshots").exists()


def test_vault_replacement_and_snapshot_size_limits_refuse(config, scope):
    baseline = run(config, scope)["snapshot_id"]
    scope[2].rename(scope[2].with_name("old-vault"))
    scope[2].mkdir()
    with pytest.raises(IntegrityError, match="scope or vault identity"):
        run(config, scope, "compare", snapshot_id=baseline)
    with patch.object(note_changes, "MAX_BYTES", 1):
        with pytest.raises(IntegrityError, match="byte limit"):
            run(config, scope)


@pytest.mark.parametrize("identity", ["../outside", "x" * 64, "A" * 64, "", "/tmp/x"])
def test_invalid_snapshot_paths_refuse(config, scope, identity):
    with pytest.raises(InvalidInput):
        run(config, scope, "compare", snapshot_id=identity)


@pytest.mark.parametrize(
    "field,value",
    [
        ("note_path", "../escape.md"),
        ("note_path", "/absolute.md"),
        ("note_path", ".hidden/a.md"),
        ("note_path", "a//b.md"),
        ("note_path", "a\\b.md"),
        ("note_path", 5),
        ("sha256", "x" * 64),
        ("sha256", None),
        ("identity", "arbitrary"),
        ("identity", "publication:invalid"),
    ],
)
def test_invalid_metadata_entries_refuse(field, value):
    entry = asdict(note_changes.Entry("path:a.md", "a.md", "a" * 64))
    entry[field] = value
    with pytest.raises(StorageError):
        note_changes.validate([entry])


def test_snapshot_headers_are_bound_even_when_digest_is_recomputed(config, scope):
    baseline = run(config, scope)["snapshot_id"]
    record = json.loads(stored(config, scope, baseline).read_bytes())
    record["client_id"] = "00000000-0000-0000-0000-000000000000"
    data = canonical(record).encode()
    forged = hashlib.sha256(data).hexdigest()
    stored(config, scope, forged).write_bytes(data)
    with pytest.raises(IntegrityError, match="scope or vault identity"):
        run(config, scope, "compare", snapshot_id=forged)


@pytest.mark.parametrize(
    "mutation", ["extra", "duplicate", "unsorted", "noncanonical", "schema", "boolean_inode"]
)
def test_rehashed_invalid_snapshots_still_refuse(config, scope, mutation):
    (scope[2] / "second.md").write_text("second content")
    baseline = run(config, scope)["snapshot_id"]
    record = json.loads(stored(config, scope, baseline).read_bytes())
    if mutation == "extra":
        record["notes"][0]["text"] = "must not be accepted"
    elif mutation == "duplicate":
        record["notes"].append(record["notes"][0])
    elif mutation == "unsorted":
        record["notes"].reverse()
    elif mutation == "schema":
        record["schema"] = "future-format"
    elif mutation == "boolean_inode":
        record["vault_ino"] = True
    data = json.dumps(record).encode() if mutation == "noncanonical" else canonical(record).encode()
    identity = hashlib.sha256(data).hexdigest()
    stored(config, scope, identity).write_bytes(data)
    with pytest.raises(StorageError):
        run(config, scope, "compare", snapshot_id=identity)


def test_unknown_identity_and_oversized_note_refuse_without_overwrite(config, scope):
    baseline = run(config, scope)["snapshot_id"]
    path = stored(config, scope, baseline)
    before = path.read_bytes()
    with pytest.raises(InvalidInput):
        run(config, scope, "check", snapshot_id=baseline, note_identity="path:unknown.md")
    (scope[2] / "huge.md").write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(IntegrityError):
        run(config, scope, "compare", snapshot_id=baseline)
    assert path.read_bytes() == before


def test_real_cli_roundtrip_conflict_and_safe_errors(config, scope):
    token = config.project_root / "client-token"
    token.write_text(scope[0]["token"])
    env = {**os.environ, "PYTHONPATH": str(Path(note_changes.__file__).parent.parent)}
    base = [sys.executable, "-m", "fiber_bundle.note_changes_cli"]
    flags = [
        "--project-root",
        str(config.project_root),
        "--config",
        "config.json",
        "--token-file",
        str(token),
        "--vault-id",
        scope[1],
    ]

    def call(action, *extra):
        return subprocess.run(
            [*base, action, *flags, *extra], env=env, capture_output=True, text=True, timeout=10
        )

    created = call("capture")
    assert created.returncode == 0
    identity = json.loads(created.stdout)["snapshot_id"]
    assert (
        call("check", "--snapshot-id", identity, "--note-identity", "path:manual.md").returncode
        == 0
    )
    (scope[2] / "manual.md").write_text("changed")
    conflict = call("check", "--snapshot-id", identity, "--note-identity", "path:manual.md")
    assert conflict.returncode == 2 and json.loads(conflict.stdout)["status"] == "conflict"
    assert call("compare", "--snapshot-id", identity).returncode == 0
    token.write_text("invalid-token")
    rejected = call("capture")
    assert rejected.returncode == 1
    assert json.loads(rejected.stdout) == {
        "status": "error",
        "error_code": "note_snapshot_rejected",
    }
    assert "invalid-token" not in rejected.stdout and "manual.md" not in rejected.stdout
