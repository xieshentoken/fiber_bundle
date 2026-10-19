import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_moc_associations import moc_scope as moc_scope
from test_moc_dispatch import dispatch, moc, schedule, state
from test_moc_dispatch import dispatch_scope as dispatch_scope
from test_moc_file_identity import TARGET, events, inventory, registration
from test_moc_file_identity import tracked as tracked
from test_moc_files import apply, invoke, plan
from test_moc_files import file_scope as file_scope
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_moves import published as published

from fiber_bundle import (
    auth,
    database,
    file_exchange,
    moc_file_journal,
    moc_files,
    moc_markdown,
    note_identities,
    note_identity_store,
    note_snapshots,
    search,
)
from fiber_bundle.config import StorageError


@pytest.fixture
def applied(config, tracked):
    scope, _ = tracked
    first = plan(config, scope)
    apply(config, scope, first)
    return tracked, first


def move(scope, relative="归档/知识/主题 MOC.md"):
    target = scope[2] / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    (scope[2] / TARGET).rename(target)
    return relative, target


def current_file(target):
    return target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns


def test_repeated_moves_preserve_identity_human_bytes_and_rebase_links(config, applied):
    tracked, first = applied
    scope, identity = tracked
    old = scope[2] / TARGET
    original = old.read_bytes()
    old.write_bytes(original + b"\r\n## Human addition\r\n[manual](../manual.md)\r\n")
    human = moc_markdown.split(old.read_bytes(), scope[3])[::2]
    before = note_snapshots.run(config, "Bearer " + scope[0]["token"], scope[1], "capture")
    previous = TARGET
    for revision, relative in enumerate(["归档/知识/主题 MOC.md", "主题 MOC.md", TARGET], 3):
        target = scope[2] / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        (scope[2] / previous).rename(target)
        observed = current_file(target)
        result = plan(config, scope, relative)
        assert result["schema"] == 2 and result["identity"]["identity"] == identity
        assert current_file(target) == observed and not (scope[2] / previous).exists()
        system = moc_markdown.split(result["markdown"].encode(), scope[3])[1]
        link = {3: b"../../manual.md", 4: b"<manual.md>", 5: b"../manual.md"}[revision]
        assert link in system
        changed = apply(config, scope, result)
        assert Path(changed["backup_path"]).read_bytes() == observed[0]
        assert moc_markdown.split(target.read_bytes(), scope[3])[::2] == human
        assert registration(config, tracked).relative_path == relative
        assert registration(config, tracked).revision == revision
        assert (
            next(n for n in inventory(config, scope).notes if n.identity == identity).relative_path
            == relative
        )
        unchanged = current_file(target)
        assert apply(config, scope, result)["moc_file_write_performed"] is False
        assert plan(config, scope, relative)["status"] == "unchanged"
        assert current_file(target) == unchanged
        previous = relative
    assert events(config, tracked) == [(1,), (2,), (3,), (4,), (5,)]
    with pytest.raises(StorageError):
        apply(config, scope, first)
    result = note_snapshots.run(
        config,
        "Bearer " + scope[0]["token"],
        scope[1],
        "check",
        snapshot_id=before["snapshot_id"],
        note_identity=identity,
    )
    # Returning to the original path with identical bytes matches the snapshot,
    # although physical identity revisions still audit each applied projection.
    assert result["status"] == "match"


@pytest.mark.parametrize("directory", [False, True])
def test_same_depth_rename_needs_no_write_or_identity_revision(config, applied, directory):
    tracked, _ = applied
    scope, identity = tracked
    if directory:
        (scope[2] / "00-Maps").rename(scope[2] / "新地图")
        relative = "新地图/topic MOC.md"
    else:
        relative, _ = move(scope, "00-Maps/renamed.md")
    target = scope[2] / relative
    original = current_file(target), registration(config, tracked), events(config, tracked)
    assert plan(config, scope, relative)["status"] == "unchanged"
    assert (
        current_file(target),
        registration(config, tracked),
        events(config, tracked),
    ) == original
    db = database.connect(config.state_root)
    try:
        results = search.search(config, db, scope[0]["id"], "Human MOC", [scope[1]])["results"]
        assert next(r for r in results if r["note_id"] == identity)["note_path"] == relative
    finally:
        db.close()
    # A later source change still generates a bound plan at the renamed location.
    (scope[2] / "manual.md").rename(scope[2] / "改名.md")
    apply(config, scope, plan(config, scope, relative))
    assert registration(config, tracked).revision == 3
    assert registration(config, tracked).relative_path == relative


@pytest.mark.parametrize(
    "kind",
    ["copy-original-visible", "copy-delete", "copy-rebind", "untracked", "untracked-then-track"],
)
def test_copies_and_unbound_history_do_not_adopt_moved_target(config, file_scope, kind):
    scope = file_scope
    if not kind.startswith("untracked"):
        note_identities.run(
            config,
            "Bearer " + scope[0]["token"],
            scope[1],
            "track",
            TARGET,
            hashlib.sha256((scope[2] / TARGET).read_bytes()).hexdigest(),
        )
    apply(config, scope, plan(config, scope))
    relative = "归档/copied.md"
    target = scope[2] / relative
    target.parent.mkdir()
    if kind.startswith("untracked"):
        (scope[2] / TARGET).rename(target)
        if kind == "untracked-then-track":
            note_identities.run(
                config,
                "Bearer " + scope[0]["token"],
                scope[1],
                "track",
                relative,
                hashlib.sha256(target.read_bytes()).hexdigest(),
            )
    else:
        shutil.copy2(scope[2] / TARGET, target)
        if kind != "copy-original-visible":
            (scope[2] / TARGET).unlink()
        if kind == "copy-rebind":
            old = next(
                r
                for r in note_identity_store.records(config, scope[1])
                if r.relative_path == TARGET
            )
            note_identities.run(
                config,
                "Bearer " + scope[0]["token"],
                scope[1],
                "rebind",
                relative,
                hashlib.sha256(target.read_bytes()).hexdigest(),
                identity=old.identity,
                revision=old.revision,
            )
    before = current_file(target)
    with pytest.raises(StorageError):
        plan(config, scope, relative)
    assert (
        current_file(target) == before
        and len(moc_file_journal.plans(config, scope[1], scope[3])) == 1
    )


@pytest.mark.parametrize(
    "edit",
    [
        "managed",
        "markers",
        "deleted",
        "old-path-occupied",
        "hidden",
        "publication",
        "cross-vault",
        "symlink",
        "hardlink",
    ],
)
def test_moved_conflicts_never_rewrite_or_resurrect(config, applied, edit):
    tracked, first = applied
    scope, _ = tracked
    relative, target = move(scope)
    if edit == "managed":
        target.write_bytes(target.read_bytes().replace(b"manual.md", b"edited.md"))
    elif edit == "markers":
        target.write_text("# Human replacement\n")
    elif edit == "deleted":
        target.unlink()
    elif edit == "old-path-occupied":
        (scope[2] / TARGET).write_text("# New human file\n")
    elif edit == "hidden":
        relative, hidden = ".hidden/moved.md", scope[2] / ".hidden/moved.md"
        hidden.parent.mkdir()
        target.rename(hidden)
        target = hidden
    elif edit == "publication":
        (target.parent / ".fiber-publication.json").write_text("{}")
    elif edit == "cross-vault":
        target.rename(config.project_root / "test vault/temp_vault2/moved.md")
    elif edit == "symlink":
        outside = config.project_root / "outside.md"
        target.rename(outside)
        target.symlink_to(outside)
    elif edit == "hardlink":
        os.link(target, target.with_name("second.md"))
    before = target.read_bytes() if target.exists() else None
    old = registration(config, tracked)
    with pytest.raises(StorageError):
        plan(config, scope, relative)
    assert (target.read_bytes() if target.exists() else None) == before
    assert registration(config, tracked) == old
    if edit != "old-path-occupied":
        assert not (scope[2] / TARGET).exists()
    with pytest.raises(StorageError):
        apply(config, scope, first)


@pytest.mark.parametrize("point", ["before-exchange", "after-exchange", "after-ledger"])
def test_moved_plan_real_process_crash_recovery_never_moves_or_rewrites(config, applied, point):
    tracked, _ = applied
    scope, identity = tracked
    relative, target = move(scope)
    planned = plan(config, scope, relative)
    token = config.project_root / "move-token.txt"
    token.write_text(scope[0]["token"])
    script = """
import os,sys
from pathlib import Path
from fiber_bundle import file_exchange,moc_files,note_identity_store
from fiber_bundle.config import load_config
root=Path(sys.argv[1]);config=load_config(root/'config.json',root)
point=sys.argv[5];rename=file_exchange.rename;save=note_identity_store.save
def exchange(*a,**k):
    if point=='before-exchange':os._exit(93)
    rename(*a,**k);os._exit(93)
def ledger(*a,**k):
    save(*a,**k);os._exit(93)
if point=='after-ledger':note_identity_store.save=ledger
else:file_exchange.rename=exchange
moc_files.run(config,'Bearer '+(root/'move-token.txt').read_text(),sys.argv[2],
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
    assert result.returncode == 93
    observed = current_file(target)
    with pytest.raises(StorageError):
        apply(config, scope, planned)
    with pytest.raises(StorageError):
        plan(config, scope, relative)
    recovered = invoke(config, scope, "reconcile-file", plan_id=planned["plan_id"])
    assert recovered["status"] == ("not-applied" if point == "before-exchange" else "applied")
    assert recovered["moc_file_write_performed"] is False and current_file(target) == observed
    assert not (scope[2] / TARGET).exists()
    assert (
        next(n for n in inventory(config, scope).notes if n.identity == identity).relative_path
        == relative
    )
    assert events(config, tracked) == (
        [(1,), (2,)] if point == "before-exchange" else [(1,), (2,), (3,)]
    )


@pytest.mark.parametrize("race", ["move-again", "same-content-copy", "old-path-occupied", "revoke"])
def test_changes_after_moved_plan_refuse_before_claim(config, applied, race):
    tracked, _ = applied
    scope, _ = tracked
    relative, target = move(scope)
    planned = plan(config, scope, relative)
    if race == "move-again":
        target.rename(target.with_name("again.md"))
    elif race == "same-content-copy":
        replacement = target.with_name("replacement.md")
        replacement.write_bytes(target.read_bytes())
        replacement.replace(target)
    elif race == "old-path-occupied":
        (scope[2] / TARGET).write_text("# User new file")
    else:
        db = database.connect(config.state_root)
        auth.set_grant(db, scope[0]["id"], scope[1], query=True, original=False, add=False)
        db.close()
    before = registration(config, tracked)
    with pytest.raises((StorageError, auth.Denied)):
        apply(config, scope, planned)
    assert registration(config, tracked) == before
    assert (
        next(
            p
            for p in moc_file_journal.plans(config, scope[1], scope[3])
            if p.plan_id == planned["plan_id"]
        ).status
        == "planned"
    )


def test_moved_old_path_race_at_exchange_compensates_without_deleting_human_file(config, applied):
    tracked, _ = applied
    scope, _ = tracked
    relative, target = move(scope)
    planned = plan(config, scope, relative)
    before = current_file(target), registration(config, tracked)
    rename = file_exchange.rename

    def race(*args, **kwargs):
        result = rename(*args, **kwargs)
        (scope[2] / TARGET).write_text("# New user file\n")
        return result

    with patch.object(file_exchange, "rename", side_effect=race), pytest.raises(StorageError):
        apply(config, scope, planned)
    assert (target.read_bytes(), target.stat().st_ino) == before[0][:2]
    assert registration(config, tracked) == before[1]
    assert (scope[2] / TARGET).read_text() == "# New user file\n"
    assert (
        next(
            p
            for p in moc_file_journal.plans(config, scope[1], scope[3])
            if p.plan_id == planned["plan_id"]
        ).status
        == "not-applied"
    )


def test_registered_dispatch_target_can_move_and_freeze_new_explicit_intent(config, dispatch_scope):
    scope = dispatch_scope
    mid, original = moc(config, scope, track=True)
    first = schedule(config, scope, (mid, original))
    assert (
        dispatch(config, scope)["processed"] == 1
        and state(config, scope, first)["state"] == "applied"
    )
    target = scope["root"] / "归档/主题/移动 MOC.md"
    target.parent.mkdir(parents=True)
    (scope["root"] / original).rename(target)
    relative = target.relative_to(scope["root"]).as_posix()
    observed = current_file(target)
    next_job = schedule(config, scope, (mid, relative), request_id=str(uuid4()))
    assert current_file(target) == observed
    assert dispatch(config, scope)["processed"] == 1
    assert state(config, scope, next_job)["state"] == "applied"
    assert not (scope["root"] / original).exists()
    assert b"../../" in target.read_bytes()
    assert len(moc_file_journal.plans(config, scope["vid"], mid)) == 2
    assert dispatch(config, scope)["processed"] == 0


def test_pending_write_after_external_move_blocks_new_plan(config, applied):
    tracked, _ = applied
    scope, _ = tracked
    (scope[2] / "manual.md").rename(scope[2] / "changed.md")
    pending = plan(config, scope)
    with (
        patch.object(moc_file_journal, "finish", side_effect=OSError("outcome")),
        pytest.raises(OSError),
    ):
        apply(config, scope, pending)
    relative, target = move(scope)
    observed = current_file(target), registration(config, tracked)
    with pytest.raises(StorageError):
        plan(config, scope, relative)
    with pytest.raises(StorageError):
        invoke(config, scope, "reconcile-file", plan_id=pending["plan_id"])
    assert (current_file(target), registration(config, tracked)) == observed
    assert not (scope[2] / TARGET).exists()


def test_move_after_planning_requires_new_plan_at_current_path(config, applied):
    tracked, _ = applied
    scope, _ = tracked
    (scope[2] / "manual.md").rename(scope[2] / "changed.md")
    stale = plan(config, scope)
    relative, target = move(scope)
    observed = current_file(target)
    with pytest.raises(StorageError):
        apply(config, scope, stale)
    assert current_file(target) == observed
    fresh = plan(config, scope, relative)
    apply(config, scope, fresh)
    assert b"../../changed.md" in target.read_bytes() and not (scope[2] / TARGET).exists()
    assert (
        next(
            p
            for p in moc_file_journal.plans(config, scope[1], scope[3])
            if p.plan_id == stale["plan_id"]
        ).status
        == "planned"
    )


@pytest.mark.parametrize("change", ["copy", "managed", "old-path"])
def test_pending_moved_recovery_preserves_conflicting_files(config, applied, change):
    tracked, _ = applied
    scope, _ = tracked
    relative, target = move(scope)
    pending = plan(config, scope, relative)
    with (
        patch.object(moc_file_journal, "finish", side_effect=OSError("outcome")),
        pytest.raises(OSError),
    ):
        apply(config, scope, pending)
    if change == "copy":
        other = target.with_name("copy.md")
        other.write_bytes(target.read_bytes())
        other.replace(target)
    elif change == "managed":
        target.write_bytes(target.read_bytes().replace(b"manual.md", b"user-edited.md"))
    else:
        (scope[2] / TARGET).write_text("# Another note")
    observed = current_file(target), registration(config, tracked)
    with pytest.raises(StorageError):
        invoke(config, scope, "reconcile-file", plan_id=pending["plan_id"])
    assert (current_file(target), registration(config, tracked)) == observed


def test_unregistered_schema3_target_is_not_adopted_after_move(config, dispatch_scope):
    scope = dispatch_scope
    mid, original = moc(config, scope)
    schedule(config, scope, (mid, original))
    assert dispatch(config, scope)["processed"] == 1
    target = scope["root"] / "移动.md"
    (scope["root"] / original).rename(target)
    with pytest.raises(StorageError):
        schedule(config, scope, (mid, "移动.md"), request_id=str(uuid4()))
    assert not (scope["root"] / original).exists()
