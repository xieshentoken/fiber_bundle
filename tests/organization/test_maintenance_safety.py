import json

import pytest
from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import auth, database, note_index
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import maintenance, store


def test_repeated_request_reuses_pending_preview(config, ready):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        first = preview(config, db, cid, vid, "quick")
        assert maintenance.submit(config, db, cid, vid, "quick")["id"] == first["id"]
        assert len(store.rows(config, "jobs", vid)) == 1


def test_confirmation_recomputes_seal_and_permission_epoch(config, ready):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "deep")
        job["items"][0]["after"] = []
        with pytest.raises(InvalidInput, match="confirmation_required"):
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        other = preview(config, db, cid, vid, "additive")
        auth.set_grant(db, cid, vid, query=True, original=True, add=False)
        auth.set_grant(db, cid, vid, query=True, original=True, add=True)
        with pytest.raises(IntegrityError, match="policy_changed"):
            maintenance.action(config, db, other, "confirm", other["plan_sha256"])


def test_all_modes_prune_policy_before_note_reads(config, ready, monkeypatch):
    cid, vid, root, *_ = ready
    excluded = "01-Projects/GF_supply_chain"
    forbidden = root / excluded
    forbidden.mkdir(parents=True)
    (forbidden / "do-not-open.md").write_text("# forbidden")
    (root / ".knowledge-organizer-policy.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "deny_paths": [
                    {
                        "path": excluded,
                        "deny": ["read", "index", "classify", "link", "write", "verify"],
                    }
                ],
            }
        )
    )
    original = note_index.read_note

    def checked(root, path, limit):
        assert not path.startswith(excluded)
        return original(root, path, limit)

    monkeypatch.setattr(note_index, "read_note", checked)
    with database.connect(config.state_root) as db:
        for mode in ("quick", "additive", "deep"):
            job = maintenance.submit(config, db, cid, vid, mode)
            assert not any(i["path"].startswith(excluded) for i in job["items"])


def test_index_retry_does_not_reapply_completed_memberships(config, ready):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "additive")
        job.update(state="completed", confirmed=True, index_state="failed", index_error="test")
        items = json.loads(json.dumps(job["items"]))
        result = maintenance.action(config, db, job, "resume", None)
        assert result["state"] == "completed" and result["index_state"] == "pending"
        assert result["items"] == items


def test_new_moc_does_not_duplicate_existing_alias(config, ready):
    cid, vid, _, moc, _ = ready
    moc.write_text('---\ntype: moc\naliases: ["Thermal"]\n---\n# 热管理 MOC\n')
    with database.connect(config.state_root) as db:
        with pytest.raises(InvalidInput, match="moc_duplicate"):
            maintenance.submit(
                config,
                db,
                cid,
                vid,
                "quick",
                [{"title": "Thermal", "topic": "热传导与换热相关主题"}],
            )


def test_quick_reuses_valid_existing_classification_without_model(config, ready):
    cid, vid, _, _, note = ready
    note.write_text("---\nmoc: '[[热管理 MOC]]'\n---\n# 已确认资料\n无需再次调用模型。\n")
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        item = job["items"][0]
        assert item["existing_reliable"] and item["evaluated"]
        assert item["target"] == "Resource/传热.md" and item["status"] == "linked"
        assert not item["model_called"] and not item["add"]


def test_old_deadline_does_not_expire_or_duplicate_unchanged_preview(config, ready):
    cid, vid, _, moc, note = ready
    before = {p: p.read_bytes() for p in (moc, note)}
    with database.connect(config.state_root) as db:
        first = preview(config, db, cid, vid, "quick")
        first["expires_at"] = 0  # Compatibility with persisted previews from older releases.
        store.put(config, "jobs", first["id"], vid, first)
        assert maintenance.submit(config, db, cid, vid, "quick")["id"] == first["id"]
        result = maintenance.action(config, db, first, "confirm", first["plan_sha256"])
        assert result["state"] == "queued" and result["confirmed"]
        assert not result["model_called"]
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_underlying_organizer_version_invalidates_old_preview(config, ready, monkeypatch):
    from fiber_bundle.organization import maintenance_plan
    from fiber_bundle.organization.contracts import ORGANIZER_VERSION

    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "deep")
        assert ORGANIZER_VERSION in job["maintenance"]
        monkeypatch.setattr(maintenance_plan, "VERSION", "maintenance/changed-organizer")
        with pytest.raises(IntegrityError, match="maintenance_version_changed"):
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])


def test_recheck_preserves_preview_without_deadline_or_applying(config, ready):
    cid, vid, _, moc, note = ready
    before = {p: p.read_bytes() for p in (moc, note)}
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        original_items = json.loads(json.dumps(job["items"]))
        job["expires_at"] = 0
        store.put(config, "jobs", job["id"], vid, job)
        queued = maintenance.action(config, db, job, "recheck", None)
        assert queued["state"] == "queued" and not queued["confirmed"]
        fresh = maintenance.run(config, db, queued)
        assert fresh["state"] == "preview" and "expires_at" not in fresh
        assert fresh["id"] == job["id"] and fresh["items"] == original_items
        assert not fresh["confirmed"] and not fresh["model_called"]
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_recheck_rejects_changed_content_and_permissions(config, ready):
    cid, vid, _, _, note = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        job["expires_at"] = 0
        note.write_text(note.read_text() + "\nchanged after preview\n")
        with pytest.raises(IntegrityError, match="preview_changed"):
            maintenance.action(config, db, job, "recheck", None)
        assert job["state"] == "preview" and job["expires_at"] == 0
        auth.set_grant(db, cid, vid, query=True, original=True, add=False)
        with pytest.raises(auth.Denied):
            maintenance.action(config, db, job, "recheck", None)


@pytest.mark.parametrize("change", ["new_note", "topic", "members", "decision"])
def test_dependency_changes_invalidate_before_confirm_without_writes(config, ready, change):
    from fiber_bundle.organization import jobs

    cid, vid, root, moc, note = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        if change == "new_note":
            (root / "Inbox/new.md").write_text("# New note\n#热管理\n")
        elif change == "topic":
            moc.write_text(moc.read_text() + "\nChanged topic\n")
        elif change == "members":
            # Use the real membership API, including its persistent revision.
            from fiber_bundle.organization import membership

            entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
            membership.apply(
                config, db, cid, vid, entities[0], mocs[0], owner="manual", record_decision=False
            )
        else:
            item = job["items"][0]
            store.put(
                config,
                "decisions",
                "synthetic-new-decision",
                vid,
                {"entity": item["entity"], "moc": job["mocs"][0]["id"], "state": "rejected"},
            )
        before = {p: p.read_bytes() for p in (moc, note)}
        assert (
            maintenance.public(job, config=config, db=db)["preview_validity"]["state"]
            == "invalidated"
        )
        with pytest.raises(IntegrityError, match="preview_changed"):
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        assert job["state"] == "preview" and not job["confirmed"]
        assert all(p.read_bytes() == raw for p, raw in before.items())
        assert maintenance.submit(config, db, cid, vid, "quick")["id"] != job["id"]


def test_elapsed_days_leave_preview_valid(config, ready, monkeypatch):
    import time

    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        seal = job["plan_sha256"]
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now + 30 * 86400)
        assert maintenance.public(job, config=config, db=db)["preview_validity"] == {
            "state": "valid"
        }
        assert maintenance.submit(config, db, cid, vid, "quick")["id"] == job["id"]
        result = maintenance.action(config, db, job, "confirm", seal)
        assert result["confirmed_sha256"] == seal and not result["model_called"]


def test_legacy_preview_ignores_deadline_and_rechecks_permission_epoch(config, ready):
    from fiber_bundle.organization import jobs

    cid, vid, _, moc, note = ready
    before = {p: p.read_bytes() for p in (moc, note)}
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid, preview=True)
        job["expires_at"] = 0
        store.put(config, "jobs", job["id"], vid, job)
        assert jobs.action(config, db, cid, vid, job["id"], "confirm")["state"] == "queued"
        job["state"] = "preview"
        store.put(config, "jobs", job["id"], vid, job)
        auth.set_grant(db, cid, vid, query=True, original=True, add=False)
        auth.set_grant(db, cid, vid, query=True, original=True, add=True)
        with pytest.raises(IntegrityError, match="authorization_changed"):
            jobs.action(config, db, cid, vid, job["id"], "confirm")
    assert all(p.read_bytes() == raw for p, raw in before.items())
