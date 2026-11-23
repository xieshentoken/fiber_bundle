"""Shared-block retained evidence keeps strict budgets and historical journal compatibility."""

import time
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_jobs import ready as ready
from test_retained_moc_members import missing_history, prepare
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
    publication_file_io,
    vault_scope,
)
from fiber_bundle.cloud_observation import ObservationError
from fiber_bundle.config import IntegrityError
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.organization import jobs, maintenance, membership, note_moc_sync
from fiber_bundle.organization import store as organization_store
from fiber_bundle.vault_sync import scanner
from fiber_bundle.vault_sync import store as sync_store


def large_missing_history(config, ready, *, repetitions=8):
    """Produce actual 36-member applied journals, then two historical missing placeholders."""
    cid, vid, root, moc, note = ready
    for index in range(35):
        path = root / "Resource" / (f"mem{index:02d}-" + "热管理" * repetitions + ".md")
        path.write_text(f"# Historical member {index}\nOriginal evidence remains here.\n")
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        assert len(entities) == 36
        initial = next(e for e in entities if e["path"] == note.relative_to(root).as_posix())
        target = mocs[0]
        membership.apply(config, db, cid, vid, initial, target)
        for entity in entities:
            note_moc_sync.track(config, db, cid, vid, entity)
        previous = moc_store.load(config, vid, target["id"])
        record = replace(
            previous,
            revision=previous.revision + 1,
            members={entity["id"]: ["system"] for entity in entities},
        )
        moc_store.save(
            config,
            vid,
            target["id"],
            cid,
            record,
            "synthetic-historical-members",
            previous.revision,
        )
        for remove_missing in (False, True):
            if remove_missing:
                missing = [e for e in entities if e["path"].startswith("Resource/")][:2]
                originals = config.project_root / "retained-originals"
                originals.mkdir()
                for entity in missing:
                    (root / entity["path"]).rename(originals / (entity["id"] + ".md"))
            # Deliberately use the original full-inventory API to create an
            # applied pre-read-scope history, including the original placeholders.
            packet = moc_files.plan_file(
                config,
                db,
                cid,
                vid,
                target["id"],
                record.revision,
                target["path"],
                digest(moc.read_bytes()),
            )
            moc_files.apply_file(config, db, cid, vid, target["id"], packet["plan_id"])
    observe(config, (cid, vid, root))
    return missing, target


def captured(config, ready, target):
    cid, vid, root, moc, _ = ready
    with database.connect(config.state_root) as db:
        vault = auth.grant(db, cid, vid, "query")
        return moc_retained_members.capture(
            config,
            vid,
            root,
            vault_scope.for_vault(root, vault),
            target["path"],
            moc_store.load(config, vid, target["id"]),
            moc.read_bytes(),
            target["id"],
        )


def refresh_observer(config, vault):
    now = time.time()
    sync_store.enqueue(config, vault, [("", 1)], str(int(now * 1000)), now=now)
    while tasks := sync_store.due(config, now=now + 10):
        for task in tasks:
            scanner.apply(config, task, now=now + 10)
    scanner.heartbeat(config, vault, now=now)


def track_moc(config, ready, target):
    cid, vid, root, _, _ = ready
    with database.connect(config.state_root) as db:
        raw, key = publication_file_io.read(root, target["path"])
        note_moc_sync.track(
            config,
            db,
            cid,
            vid,
            {
                "kind": "note",
                "id": "note:" + str(uuid4()),
                "path": target["path"],
                "sha256": digest(raw),
                "file_key": key,
            },
        )


def test_large_shared_block_mount_retains_two_missing_members(config, ready, package):
    cid, vid, root, moc, note = ready
    missing, target = large_missing_history(config, ready)
    retained = captured(config, ready, target)
    legacy = moc_retained_members.proofs(retained)
    assert 11000 <= len(next(iter(retained["blocks"].values())).encode()) <= 12500
    assert len(canonical(legacy).encode()) > moc_retained_members.MAX_BYTES
    assert len(canonical(retained).encode()) < moc_retained_members.MAX_BYTES
    with pytest.raises(IntegrityError, match="moc_retained_member_limit"):
        moc_retained_members.validate(legacy, target=target["path"])
    assert retained["schema"] == "moc-retained-members/v2"
    assert len(retained["blocks"]) == 1 and len(retained["members"]) == 2
    expert, current, before = prepare(config, ready, target, package)
    registrations = note_identity_store.records(config, vid)
    with database.connect(config.state_root) as db:
        assert not membership.apply(config, db, cid, vid, expert, target)
    after = moc.read_bytes()
    assert after != before
    assert moc_store.load(config, vid, target["id"]) == current
    assert note_identity_store.records(config, vid) == registrations
    for entity in missing:
        assert f"- 当前不可见：{entity['id']}（归属保留）".encode() in after
        assert not (root / entity["path"]).exists()
    assert note.exists()
    assert (
        moc_markdown.split(before, target["id"])[::2]
        == moc_markdown.split(after, target["id"])[::2]
    )
    latest = moc_file_journal.latest(moc_file_journal.plans(config, vid, target["id"]))
    assert len((latest.path / "plan.json").read_bytes()) <= 65536
    moc_write_scope.validate(latest.value["read_scope"])
    refresh_observer(config, vid)
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
        maintenance.run(config, db, job)
        assert len(job["items"]) == 1
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        maintenance.run(config, db, job)
        assert job["state"] == "completed", job
        assert job["items"][0]["phase"] == "archived"
    assert not note.exists() and (root / "Resource" / note.name).exists()
    for entity in missing:
        assert f"- 当前不可见：{entity['id']}（归属保留）".encode() in moc.read_bytes()


@pytest.mark.parametrize(
    "fault",
    ["block", "reference", "missing_ref", "extra_block", "schema", "owners", "registration"],
)
def test_shared_block_wire_tampering_never_changes_the_original_evidence(config, ready, fault):
    _, target = large_missing_history(config, ready)
    retained = captured(config, ready, target)
    original = deepcopy(retained)
    identity, proof = next(iter(retained["members"].items()))
    reference = proof["block_ref"]
    if fault == "block":
        retained["blocks"][reference] += "Human alteration\n"
    elif fault == "reference":
        proof["block_ref"] = "0" * 64
    elif fault == "missing_ref":
        del retained["blocks"][reference]
    elif fault == "extra_block":
        retained["blocks"]["0" * 64] = "unreferenced"
    elif fault == "schema":
        retained["schema"] = "moc-retained-members/v9"
    elif fault == "owners":
        proof["owners"] = ["system", "manual"]
    else:
        proof["registration"]["identity"] = "note:" + str(uuid4())
    with pytest.raises(IntegrityError):
        moc_retained_members.validate(retained)
    assert moc_retained_members.validate(original) == original
    assert identity in moc_retained_members.proofs(original)


@pytest.mark.parametrize("tracked", [False, True])
def test_v2_pending_journal_recovers_after_restart_without_another_file_exchange(
    config, ready, package, tracked
):
    cid, vid, _, moc, _ = ready
    _, target = large_missing_history(config, ready)
    if tracked:
        track_moc(config, ready, target)
    expert, _, before = prepare(config, ready, target, package)
    with database.connect(config.state_root) as db:
        with patch.object(moc_file_journal, "finish", side_effect=OSError("after MOC exchange")):
            with pytest.raises(OSError):
                membership.apply(config, db, cid, vid, expert, target)
        pending = next(
            p for p in moc_file_journal.plans(config, vid, target["id"]) if p.status == "pending"
        )
        wire = (pending.path / "plan.json").read_bytes()
        assert pending.value["schema"] == (2 if tracked else 1)
        after = moc.read_bytes(), moc.stat().st_ino
        assert after[0] != before
    with database.connect(config.state_root) as db:
        index_id = "index:" + vid + ":" + cid
        index_job = organization_store.get(config, "jobs", index_id)
        index_job["index_state"] = "ready"
        organization_store.put(config, "jobs", index_id, vid, index_job)
        assert not membership.apply(config, db, cid, vid, expert, target)
        assert organization_store.get(config, "jobs", index_id)["index_state"] == "pending"
        assert not membership.apply(config, db, cid, vid, expert, target)
    assert (moc.read_bytes(), moc.stat().st_ino) == after
    assert (pending.path / "plan.json").read_bytes() == wire
    assert (
        next(
            p
            for p in moc_file_journal.plans(config, vid, target["id"])
            if p.plan_id == pending.plan_id
        ).status
        == "applied"
    )


@pytest.mark.parametrize("tracked", [False, True])
def test_legacy_retained_pending_journal_recovers_without_rewriting_its_wire(
    config, ready, package, tracked
):
    cid, vid, _, moc, _ = ready
    _, target = missing_history(config, ready)
    if tracked:
        track_moc(config, ready, target)
    expert, current, before = prepare(config, ready, target, package)
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
        scope["retained_members"] = moc_retained_members.proofs(scope["retained_members"])
        moc_write_scope.validate(scope)
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
        with patch.object(moc_file_journal, "finish", side_effect=OSError("legacy exchange crash")):
            with pytest.raises(OSError):
                moc_files.apply_file(config, db, cid, vid, target["id"], packet["plan_id"])
        plan = next(
            p
            for p in moc_file_journal.plans(config, vid, target["id"])
            if p.plan_id == packet["plan_id"]
        )
        wire = (plan.path / "plan.json").read_bytes()
        after = moc.read_bytes(), moc.stat().st_ino
    with database.connect(config.state_root) as db:
        assert not membership.apply(config, db, cid, vid, expert, target)
    assert (plan.path / "plan.json").read_bytes() == wire
    assert (plan.path / "outcome").read_bytes() == b"applied\n"
    assert (moc.read_bytes(), moc.stat().st_ino) == after


def pending_mount(config, ready, package):
    cid, vid, _, moc, _ = ready
    _, target = large_missing_history(config, ready)
    expert, _, _ = prepare(config, ready, target, package)
    with database.connect(config.state_root) as db:
        with patch.object(moc_file_journal, "finish", side_effect=OSError("synthetic pending")):
            with pytest.raises(OSError):
                membership.apply(config, db, cid, vid, expert, target)
    pending = next(
        p for p in moc_file_journal.plans(config, vid, target["id"]) if p.status == "pending"
    )
    return expert, target, pending


@pytest.mark.parametrize("fault", ["revoked", "target", "candidate", "members"])
def test_pending_consumer_never_advances_invalid_original_intent(config, ready, package, fault):
    cid, vid, _, moc, _ = ready
    expert, target, pending = pending_mount(config, ready, package)
    with database.connect(config.state_root) as db:
        if fault == "revoked":
            auth.set_grant(db, cid, vid, query=True, original=True, add=False)
        elif fault == "target":
            moc.write_text(moc.read_text() + "\nHuman edit after exchange\n")
        elif fault == "candidate":
            (pending.path / "candidate.md").write_text("Changed displaced original\n")
        else:
            current = moc_store.load(config, vid, target["id"])
            moc_store.save(
                config,
                vid,
                target["id"],
                cid,
                replace(
                    current,
                    revision=current.revision + 1,
                    members={**current.members, "note:" + str(uuid4()): ["manual"]},
                ),
                "synthetic-new-intent",
                current.revision,
            )
        before = moc.read_bytes(), moc_store.load(config, vid, target["id"])
        wire = (pending.path / "plan.json").read_bytes()
        with pytest.raises((IntegrityError, auth.Denied)):
            membership.apply(config, db, cid, vid, expert, target)
    assert (pending.path / "plan.json").read_bytes() == wire
    assert not (pending.path / "outcome").exists()
    assert (moc.read_bytes(), moc_store.load(config, vid, target["id"])) == before


def test_new_member_is_written_only_after_the_original_pending_revision_is_settled(
    config, ready, package
):
    cid, vid, root, _, _ = ready
    _, target, pending = pending_mount(config, ready, package)
    (root / "Inbox/new-member.md").write_text("# Newly requested member\nUser content remains.\n")
    refresh_observer(config, vid)
    with database.connect(config.state_root) as db:
        entities, _, _ = jobs.snapshot(config, db, cid, vid)
        entity = next(e for e in entities if e["path"] == "Inbox/new-member.md")
        save = moc_store.save

        def checked_save(*args, **kwargs):
            assert (pending.path / "outcome").read_bytes() == b"applied\n"
            return save(*args, **kwargs)

        with patch.object(moc_store, "save", checked_save):
            assert membership.apply(config, db, cid, vid, entity, target)
    assert (pending.path / "outcome").read_bytes() == b"applied\n"
    assert entity["id"] in moc_store.load(config, vid, target["id"]).members


def test_existing_decision_and_unlock_shortcuts_do_not_consume_pending_files(
    config, ready, package
):
    cid, vid, _, moc, _ = ready
    expert, target, pending = pending_mount(config, ready, package)
    before = moc.read_bytes(), moc_store.load(config, vid, target["id"])
    key = membership.decision_key(vid, expert["id"], target["id"])
    with database.connect(config.state_root) as db:
        for state in ("pinned", "rejected", "retired"):
            organization_store.put(
                config,
                "decisions",
                key,
                vid,
                {"entity": expert["id"], "moc": target["id"], "state": state},
            )
            assert not membership.apply(config, db, cid, vid, expert, target)
            assert not (pending.path / "outcome").exists()
        assert not membership.apply(config, db, cid, vid, expert, target, action="unlock")
    assert (moc.read_bytes(), moc_store.load(config, vid, target["id"])) == before
    assert not (pending.path / "outcome").exists()


@pytest.mark.parametrize("field", ["moc_id", "base_id", "base_revision"])
def test_valid_shared_block_reference_cannot_be_transplanted_to_another_context(
    config, ready, field
):
    _, vid, root, _, _ = ready
    _, target = large_missing_history(config, ready)
    value = captured(config, ready, target)
    proof = next(iter(value["members"].values()))
    proof[field] = (
        str(uuid4()) if field == "moc_id" else "0" * 64 if field == "base_id" else proof[field] + 1
    )
    moc_retained_members.validate(value)
    with pytest.raises(IntegrityError):
        moc_retained_members.check(config, vid, root, vault_scope.Scope(), target["path"], value)


def test_shared_wire_keeps_real_24k_aggregate_limit_and_bounded_diagnostic(config, ready):
    cid, vid, _, moc, _ = ready
    missing, target = large_missing_history(config, ready, repetitions=18)
    before = moc.read_bytes(), note_identity_store.records(config, vid)
    assert len(moc_markdown.split(before[0], target["id"])[1]) < moc_retained_members.MAX_BYTES
    with database.connect(config.state_root) as db:
        with pytest.raises(ObservationError, match="moc_retained_member_limit") as caught:
            moc_write_scope.prepare(
                config,
                db,
                auth.grant(db, cid, vid, "query"),
                target["path"],
                moc_store.load(config, vid, target["id"]),
                moc.read_bytes(),
                target["id"],
            )
    detail = caught.value.detail
    assert detail["reason"] == "retained_evidence_limit"
    assert detail["phase"] == "moc_retained_members"
    assert detail["path"] == target["path"] and detail["moc_id"] == target["id"]
    assert detail["actual_bytes"] > detail["limit_bytes"] == 24576
    assert set(detail["missing_members"]) == {e["path"] for e in missing}
    assert not detail["retryable_read"]
    assert (moc.read_bytes(), note_identity_store.records(config, vid)) == before


@pytest.mark.parametrize("fault", ["reappeared", "relocated", "scope", "root", "cloud"])
def test_shared_evidence_rechecks_actual_absence_location_and_permission(
    config, ready, fault, monkeypatch
):
    _, vid, root, moc, _ = ready
    missing, target = large_missing_history(config, ready)
    value = captured(config, ready, target)
    original = config.project_root / "retained-originals" / (missing[0]["id"] + ".md")
    scope = vault_scope.Scope()
    checked_root = root
    if fault == "reappeared":
        original.rename(root / missing[0]["path"])
    elif fault == "relocated":
        original.rename(root / "Resource/relocated.md")
        sync_store.enqueue(config, vid, [("", 1)], "100", now=100)
        while tasks := sync_store.due(config, now=110):
            for task in tasks:
                scanner.apply(config, task, now=110)
    elif fault == "scope":
        scope = vault_scope.Scope(exclude=("Resource",))
    elif fault == "root":
        checked_root = config.project_root / "different-root"
        checked_root.mkdir()
    else:
        local = cloud_files.require_local

        def unavailable(path):
            if path == root / missing[0]["path"]:
                raise ObservationError("cloud_metadata_unavailable", path, "metadata_unavailable")
            return local(path)

        monkeypatch.setattr(cloud_files, "require_local", unavailable)
    before = moc.read_bytes()
    with pytest.raises(IntegrityError):
        moc_retained_members.check(config, vid, checked_root, scope, target["path"], value)
    assert moc.read_bytes() == before
