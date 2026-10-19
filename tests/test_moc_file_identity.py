import hashlib
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from test_moc_associations import moc_scope as moc_scope
from test_moc_files import apply, invoke, plan
from test_moc_files import file_scope as file_scope
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_identities import track

from fiber_bundle import (
    auth,
    database,
    file_exchange,
    moc_file_identity,
    moc_file_journal,
    moc_files,
    note_identity_store,
    note_index,
    note_inventory,
    note_snapshots,
    search,
)
from fiber_bundle.config import StorageError

TARGET = "00-Maps/topic MOC.md"


@pytest.fixture
def tracked(config, file_scope):
    registered = track(config, file_scope[:3], TARGET)
    return file_scope, registered["note_identity"]


def registration(config, tracked):
    scope, identity = tracked
    return next(r for r in note_identity_store.records(config, scope[1]) if r.identity == identity)


def events(config, tracked):
    with note_identity_store.connection(config) as db:
        return [
            row[:]
            for row in db.execute("SELECT revision FROM events WHERE identity=?", (tracked[1],))
        ]


def inventory(config, scope):
    db = database.connect(config.state_root)
    try:
        return note_inventory.read(config, db, auth.grant(db, scope[0]["id"], scope[1], "query"))
    finally:
        db.close()


def test_updates_keep_stable_id_audit_and_current_evidence(config, tracked):
    scope, identity = tracked
    target = scope[2] / TARGET
    original, old = target.read_bytes(), registration(config, tracked)
    snapshot = note_snapshots.run(config, "Bearer " + scope[0]["token"], scope[1], "capture")
    planned = plan(config, scope)
    assert planned["schema"] == 2 and planned["identity"]["identity"] == identity
    assert registration(config, tracked) == old and target.read_bytes() == original
    result = apply(config, scope, planned)
    new = registration(config, tracked)
    assert new.identity == old.identity and new.revision == 2 and new.file_key != old.file_key
    assert new.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert Path(result["backup_path"]).read_bytes() == original
    assert (
        note_index.read_note(Path(result["backup_path"]).parent, "candidate.md", 1048576).file_key
        == old.file_key
    )
    assert (
        next(n for n in inventory(config, scope).notes if n.relative_path == TARGET).identity
        == identity
    )
    db = database.connect(config.state_root)
    try:
        result = search.search(config, db, scope[0]["id"], "Human MOC", [scope[1]])
        assert next(r for r in result["results"] if r["note_path"] == TARGET)["note_id"] == identity
    finally:
        db.close()
    checked = note_snapshots.run(
        config,
        "Bearer " + scope[0]["token"],
        scope[1],
        "check",
        snapshot_id=snapshot["snapshot_id"],
        note_identity=identity,
    )
    assert checked["status"] == "conflict" and checked["reason"] == "edited"
    assert apply(config, scope, planned)["moc_file_write_performed"] is False
    assert events(config, tracked) == [(1,), (2,)]
    target.write_bytes(target.read_bytes() + b"\n## Human addition\nkeep me\n")
    (scope[2] / "manual.md").rename(scope[2] / "renamed.md")
    second = plan(config, scope)
    apply(config, scope, second)
    assert target.read_bytes().startswith(original) and target.read_bytes().endswith(b"keep me\n")
    assert (
        registration(config, tracked).identity == identity
        and registration(config, tracked).revision == 3
    )
    assert events(config, tracked) == [(1,), (2,), (3,)]


@pytest.mark.parametrize("point", ["before-exchange", "after-exchange", "after-ledger"])
def test_real_process_crash_and_explicit_identity_recovery(config, tracked, point):
    scope, identity = tracked
    planned = plan(config, scope)
    target = scope[2] / TARGET
    old = registration(config, tracked)
    original = target.read_bytes()
    token = config.project_root / "crash-token.txt"
    token.write_text(scope[0]["token"])
    script = """
import os,sys
from pathlib import Path
from fiber_bundle import file_exchange,moc_files,note_identity_store
from fiber_bundle.config import load_config
root=Path(sys.argv[1]);config=load_config(root/'config.json',root)
point=sys.argv[5]
rename=file_exchange.rename
save=note_identity_store.save
def crash_exchange(*args,**kwargs):
    if point=='before-exchange': os._exit(91)
    rename(*args,**kwargs)
    os._exit(91)
def crash_ledger(*args,**kwargs):
    save(*args,**kwargs)
    os._exit(91)
if point=='after-ledger': note_identity_store.save=crash_ledger
else: file_exchange.rename=crash_exchange
moc_files.run(config,'Bearer '+(root/'crash-token.txt').read_text(),sys.argv[2],
 'apply-file',sys.argv[3],plan_id=sys.argv[4])
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(config.project_root),
            scope[1],
            scope[3],
            planned["plan_id"],
            point,
        ],
        timeout=20,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(moc_files.__file__).parents[1])},
    )
    assert result.returncode == 91
    assert moc_file_journal.plans(config, scope[1], scope[3])[0].status == "pending"
    assert registration(config, tracked).revision == (2 if point == "after-ledger" else 1)
    if point == "after-exchange":
        with pytest.raises(StorageError):
            inventory(config, scope)
    with pytest.raises(StorageError):
        apply(config, scope, planned)
    with pytest.raises(StorageError):
        plan(config, scope)
    before = target.read_bytes(), target.stat().st_ino
    recovered = invoke(config, scope, "reconcile-file", plan_id=planned["plan_id"])
    assert recovered["status"] == ("not-applied" if point == "before-exchange" else "applied")
    assert recovered["moc_file_write_performed"] is False
    assert (target.read_bytes(), target.stat().st_ino) == before
    assert (
        next(n for n in inventory(config, scope).notes if n.relative_path == TARGET).identity
        == identity
    )
    if point == "before-exchange":
        assert registration(config, tracked) == old and target.read_bytes() == original
        assert events(config, tracked) == [(1,)]
    else:
        assert events(config, tracked) == [(1,), (2,)]
        invoke(config, scope, "reconcile-file", plan_id=planned["plan_id"])
        assert events(config, tracked) == [(1,), (2,)]


@pytest.mark.parametrize("failure", ["event-transaction", "save-before", "save-after", "outcome"])
def test_ledger_failures_keep_file_and_registration_consistent(config, tracked, failure):
    scope, _ = tracked
    old = registration(config, tracked)
    target = scope[2] / TARGET
    original, inode = target.read_bytes(), target.stat().st_ino
    planned = plan(config, scope)
    if failure == "event-transaction":
        with note_identity_store.connection(config, write=True) as db:
            db.execute(
                "CREATE TRIGGER fail_event BEFORE INSERT ON events "
                "BEGIN SELECT RAISE(ABORT,'event failure'); END"
            )
        failing = patch.object(moc_file_identity, "current", wraps=moc_file_identity.current)
    elif failure == "save-before":
        failing = patch.object(note_identity_store, "save", side_effect=OSError("before commit"))
    elif failure == "save-after":
        save = note_identity_store.save

        def fail(*args, **kwargs):
            save(*args, **kwargs)
            raise OSError("after commit")

        failing = patch.object(note_identity_store, "save", side_effect=fail)
    else:
        failing = patch.object(moc_file_journal, "finish", side_effect=OSError("outcome failure"))
    with failing, pytest.raises((OSError, sqlite3.Error)):
        apply(config, scope, planned)
    committed = failure in {"save-after", "outcome"}
    if committed:
        assert target.read_bytes() != original and registration(config, tracked).revision == 2
        assert moc_file_journal.plans(config, scope[1], scope[3])[0].status == "pending"
        invoke(config, scope, "reconcile-file", plan_id=planned["plan_id"])
        assert events(config, tracked) == [(1,), (2,)]
    else:
        assert (target.read_bytes(), target.stat().st_ino) == (original, inode)
        assert registration(config, tracked) == old and events(config, tracked) == [(1,)]
        assert moc_file_journal.plans(config, scope[1], scope[3])[0].status == "not-applied"


@pytest.mark.parametrize(
    "change", ["revision", "same-content-target", "same-content-candidate", "unrelated"]
)
def test_stale_binding_or_unrelated_conflict_blocks_before_claim(config, tracked, change):
    scope, _ = tracked
    target = scope[2] / TARGET
    planned = plan(config, scope)
    saved = moc_file_journal.plans(config, scope[1], scope[3])[0]
    if change == "revision":
        old = registration(config, tracked)
        note_identity_store.save(
            config, scope[1], scope[0]["id"], replace(old, revision=2), expected_revision=1
        )
    else:
        path = target if change == "same-content-target" else saved.path / "candidate.md"
        if change == "unrelated":
            path = scope[2] / "manual.md"
        other = path.with_name("replacement.md")
        other.write_bytes(path.read_bytes())
        other.replace(path)
    before = target.read_bytes(), target.stat().st_ino
    with pytest.raises(StorageError):
        apply(config, scope, planned)
    assert (target.read_bytes(), target.stat().st_ino) == before
    assert moc_file_journal.plans(config, scope[1], scope[3])[0].status == "planned"


@pytest.mark.parametrize(
    "conflict", ["same-content-target", "backup", "registration", "unrelated", "revoke"]
)
def test_pending_recovery_refuses_conflicting_evidence(config, tracked, conflict):
    scope, _ = tracked
    planned = plan(config, scope)
    with (
        patch.object(moc_file_identity, "complete", side_effect=SystemExit),
        pytest.raises(SystemExit),
    ):
        apply(config, scope, planned)
    target = scope[2] / TARGET
    saved = moc_file_journal.plans(config, scope[1], scope[3])[0]
    if conflict in {"same-content-target", "backup", "unrelated"}:
        path = target if conflict == "same-content-target" else saved.path / "candidate.md"
        if conflict == "unrelated":
            path = scope[2] / "manual.md"
        other = path.with_name("replacement.md")
        other.write_bytes(path.read_bytes())
        other.replace(path)
    elif conflict == "registration":
        old = registration(config, tracked)
        note_identity_store.save(
            config, scope[1], scope[0]["id"], replace(old, revision=2), expected_revision=1
        )
    else:
        db = database.connect(config.state_root)
        auth.set_grant(db, scope[0]["id"], scope[1], query=True, original=False, add=False)
        db.close()
    before = target.read_bytes(), target.stat().st_ino, registration(config, tracked)
    with pytest.raises((StorageError, auth.Denied)):
        invoke(config, scope, "reconcile-file", plan_id=planned["plan_id"])
    assert (target.read_bytes(), target.stat().st_ino, registration(config, tracked)) == before
    assert moc_file_journal.plans(config, scope[1], scope[3])[0].status == "pending"


def test_legacy_untracked_plan_cannot_adopt_later_registration(config, file_scope):
    planned = plan(config, file_scope)
    assert planned["schema"] == 1
    registered = track(config, file_scope[:3], TARGET)
    old = registration(config, (file_scope, registered["note_identity"]))
    with pytest.raises(StorageError):
        apply(config, file_scope, planned)
    assert registration(config, (file_scope, registered["note_identity"])) == old


def test_registered_target_revocation_at_exchange_rolls_back(config, tracked):
    scope, _ = tracked
    target = scope[2] / TARGET
    old = registration(config, tracked)
    original, inode = target.read_bytes(), target.stat().st_ino
    planned = plan(config, scope)
    rename = file_exchange.rename
    calls = 0

    def revoke(*args, **kwargs):
        nonlocal calls
        result = rename(*args, **kwargs)
        calls += 1
        if calls == 1:
            db = database.connect(config.state_root)
            auth.set_grant(db, scope[0]["id"], scope[1], query=False, original=False, add=True)
            db.close()
        return result

    with patch.object(file_exchange, "rename", side_effect=revoke), pytest.raises(auth.Denied):
        apply(config, scope, planned)
    assert registration(config, tracked) == old
    assert (target.read_bytes(), target.stat().st_ino) == (original, inode)


def test_missing_candidate_birth_never_publishes_tracked_plan(config, tracked):
    scope, _ = tracked
    read = note_index.read_note

    def no_birth(root, relative, limit):
        note = read(root, relative, limit)
        return replace(note, file_key=None) if relative == "candidate.md" else note

    with patch.object(note_index, "read_note", side_effect=no_birth), pytest.raises(StorageError):
        plan(config, scope)
    assert registration(config, tracked).revision == 1
    assert moc_file_journal.plans(config, scope[1], scope[3]) == []
    staged = list(config.state_root.glob(".moc-candidate-*"))
    assert len(staged) == 1 and (staged[0] / "candidate.md").exists()


@pytest.mark.parametrize(
    "bad", ["birth", "boolean-inode", "extra-field", "publication", "vault", "schema"]
)
def test_tracked_plan_contract_rejects_malformed_or_mismatched_identity(config, tracked, bad):
    scope, _ = tracked
    planned = plan(config, scope)
    value = moc_file_journal.plans(config, scope[1], scope[3])[0].value
    if bad == "birth":
        value["after_file_key"][2] = "nan"
    elif bad == "boolean-inode":
        value["after_file_key"][1] = True
    elif bad == "extra-field":
        value["identity"]["unknown"] = 1
    elif bad == "publication":
        value["identity"]["publication_id"] = scope[3]
    elif bad == "vault":
        value["identity"]["vault_key"][1] += 1
    else:
        value["schema"] = True
    with pytest.raises(StorageError):
        moc_file_journal.validate(value, scope[1], scope[3])
    assert apply(config, scope, planned)["status"] == "applied"


def test_visible_original_blocks_pending_identity_recovery(config, tracked):
    scope, _ = tracked
    planned = plan(config, scope)
    with (
        patch.object(moc_file_identity, "complete", side_effect=SystemExit),
        pytest.raises(SystemExit),
    ):
        apply(config, scope, planned)
    saved = moc_file_journal.plans(config, scope[1], scope[3])[0]
    (saved.path / "candidate.md").rename(scope[2] / "visible-original.md")
    old = registration(config, tracked)
    with pytest.raises(StorageError):
        invoke(config, scope, "reconcile-file", plan_id=planned["plan_id"])
    assert registration(config, tracked) == old
    assert moc_file_journal.plans(config, scope[1], scope[3])[0].status == "pending"


def test_registered_root_move_at_exchange_rolls_back_without_rebinding(config, tracked):
    scope, _ = tracked
    planned = plan(config, scope)
    root = scope[2]
    old = registration(config, tracked)
    original = (root / TARGET).read_bytes()
    moved = root.with_name("old-root")
    rename = file_exchange.rename
    calls = 0

    def move_root(*args, **kwargs):
        nonlocal calls
        result = rename(*args, **kwargs)
        calls += 1
        if calls == 1:
            root.rename(moved)
            root.mkdir()
        return result

    with patch.object(file_exchange, "rename", side_effect=move_root), pytest.raises(StorageError):
        apply(config, scope, planned)
    assert registration(config, tracked) == old
    assert (moved / TARGET).read_bytes() == original and not list(root.iterdir())
