"""Only exact, previously applied missing-member placeholders survive a new mount."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_jobs import ready as ready
from test_vault_sync_reconcile import observe

from fiber_bundle import (
    auth,
    cloud_files,
    database,
    moc_file_journal,
    moc_files,
    moc_markdown,
    moc_retained_members,
    moc_store,
    moc_write_scope,
    note_identity_store,
)
from fiber_bundle.cloud_observation import ObservationError
from fiber_bundle.config import IntegrityError
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.organization import jobs, membership, overview, packages
from fiber_bundle.vault_sync import scanner, store


def missing_history(config, ready, *, placeholder=True):
    cid, vid, root, moc, note = ready
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        entity, target = entities[0], mocs[0]
        membership.apply(config, db, cid, vid, entity, target)
        note.unlink()
        if placeholder:
            # This is the unchanged legacy journal behavior. It supplies an
            # actual applied old placeholder rather than forging a new proof.
            current = moc_store.load(config, vid, target["id"])
            packet = moc_files.plan_file(
                config,
                db,
                cid,
                vid,
                target["id"],
                current.revision,
                target["path"],
                digest(moc.read_bytes()),
            )
            moc_files.apply_file(config, db, cid, vid, target["id"], packet["plan_id"])
    observe(config, (cid, vid, root))
    return entity, target


def prepare(config, ready, target, package):
    cid, vid, _, moc, _ = ready
    with database.connect(config.state_root) as db:
        registered = packages.register(config, db, cid, vid, "Book/fake-thermal")
        expert = {"kind": "expert", "id": "expert:" + registered["id"], "path": registered["path"]}
        current = moc_store.load(config, vid, target["id"])
        # Model analysis and metadata had committed; only the file projection
        # remains. Recovery must preserve the old member and avoid new analysis.
        current = replace(
            current,
            revision=current.revision + 1,
            members={**current.members, expert["id"]: ["system"]},
        )
        moc_store.save(
            config, vid, target["id"], cid, current, "fixture-half-complete", current.revision - 1
        )
    store.enqueue(config, vid, [("", 1)], "20", now=20)
    while tasks := store.due(config, now=30):
        for task in tasks:
            scanner.apply(config, task, now=30)
    return expert, current, moc.read_bytes()


def test_half_committed_expert_mount_preserves_exact_old_placeholder_and_ledgers(
    config, ready, package, monkeypatch
):
    cid, vid, _, moc, _ = ready
    missing, target = missing_history(config, ready)
    expert, current, before = prepare(config, ready, target, package)
    original_registrations = note_identity_store.records(config, vid)
    old_plans = {
        p.plan_id: (p.path / "plan.json").read_bytes()
        for p in moc_file_journal.plans(config, vid, target["id"])
    }
    package_bytes = {
        p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()
    }
    with database.connect(config.state_root) as db:
        assert not membership.apply(config, db, cid, vid, expert, target)
        after = moc.read_bytes()
        assert not membership.apply(config, db, cid, vid, expert, target)
    assert moc.read_bytes() == after and after != before
    assert moc_store.load(config, vid, target["id"]) == current
    assert note_identity_store.records(config, vid) == original_registrations
    assert package_bytes == {
        p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()
    }
    assert (
        moc_markdown.split(before, target["id"])[::2]
        == moc_markdown.split(after, target["id"])[::2]
    )
    line = f"- 当前不可见：{missing['id']}（归属保留）".encode()
    assert before.count(line) == after.count(line) == 1
    assert after.count(expert["id"].encode()) == 1
    latest = moc_file_journal.latest(moc_file_journal.plans(config, vid, target["id"]))
    proof = moc_retained_members.proofs(latest.value["read_scope"]["retained_members"])[
        missing["id"]
    ]
    assert digest(proof["block"].encode()) == proof["managed_sha256"]
    assert proof["registration"]["identity"] == missing["id"]
    assert all(
        (latest.path.parent / key / "plan.json").read_bytes() == value
        for key, value in old_plans.items()
    )
    expected_issues = [
        {
            "moc_id": target["id"],
            "identity": missing["id"],
            "path": missing["path"],
            "reason": "moc_historical_member_missing",
        }
    ]
    with monkeypatch.context() as guarded:
        root = ready[2]
        for name in ("stat", "lstat", "read_bytes", "open"):
            original = getattr(Path, name)

            def no_vault(path, *args, _original=original, **kwargs):
                assert path != root and root not in path.parents
                return _original(path, *args, **kwargs)

            guarded.setattr(Path, name, no_vault)
        assert moc_retained_members.issues(config, vid) == expected_issues
    with database.connect(config.state_root) as db:
        assert overview.progress(config, db, cid, vid)["retained_member_issues"] == expected_issues
    assert (
        moc.read_bytes() == after
        and note_identity_store.records(config, vid) == original_registrations
    )


def test_current_missing_member_without_applied_placeholder_is_not_admitted(config, ready, package):
    cid, vid, _, moc, _ = ready
    _, target = missing_history(config, ready, placeholder=False)
    expert, current, before = prepare(config, ready, target, package)
    with database.connect(config.state_root) as db:
        with pytest.raises(IntegrityError, match="moc_write_scope_member_unavailable"):
            membership.apply(config, db, cid, vid, expert, target)
    assert moc.read_bytes() == before and moc_store.load(config, vid, target["id"]) == current


@pytest.mark.parametrize("fault", ["reappeared", "registration", "scope", "cloud"])
def test_retained_evidence_changes_after_plan_never_write(
    config, ready, package, monkeypatch, fault
):
    cid, vid, root, moc, note = ready
    missing, target = missing_history(config, ready)
    _, current, before = prepare(config, ready, target, package)
    with database.connect(config.state_root) as db:
        scope = moc_write_scope.prepare(
            config,
            db,
            auth.grant(db, cid, vid, "query"),
            target["path"],
            current,
            before,
            target["id"],
        )
        packet = moc_files.plan_file(
            config,
            db,
            cid,
            vid,
            target["id"],
            current.revision,
            target["path"],
            digest(before),
            read_scope=scope,
        )
        if fault == "reappeared":
            note.write_text("# User-created replacement\n")
        elif fault == "registration":
            original = next(
                r for r in note_identity_store.records(config, vid) if r.identity == missing["id"]
            )
            note_identity_store.save(
                config,
                vid,
                cid,
                replace(original, revision=original.revision + 1),
                expected_revision=original.revision,
            )
        elif fault == "scope":
            db.execute(
                "UPDATE vaults SET scope_json=? WHERE id=?",
                (canonical({"include": [""], "exclude": ["Inbox"]}), vid),
            )
        else:
            original = cloud_files.require_local

            def cloud(path: Path):
                if path == root / missing["path"]:
                    raise ObservationError(
                        "cloud_metadata_unavailable", path, "metadata_unavailable"
                    )
                original(path)

            monkeypatch.setattr(cloud_files, "require_local", cloud)
        with pytest.raises(IntegrityError):
            moc_files.apply_file(config, db, cid, vid, target["id"], packet["plan_id"])
    assert moc.read_bytes() == before
