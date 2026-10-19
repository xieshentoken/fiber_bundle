import pytest
from test_publication_layout import activate

from fiber_bundle import database, moc_store
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import jobs, membership
from fiber_bundle.vault_save_contract import settings_wire, unified_settings


@pytest.fixture
def ready(config, scope):
    cid, vid, root = scope
    settings = settings_wire(unified_settings())
    settings.pop("layout_mode")
    activate(config, vid, **settings)
    m = root / "Map/热管理 MOC.md"
    m.write_text("---\ntype: moc\n---\n# 热管理 MOC\n\n人工内容保留\n")
    n = root / "Inbox/传热.md"
    n.write_text("# 传热\n\n#热管理\n傅里叶定律。\n")
    return cid, vid, root, m, n


def test_run_preserves_bytes_and_is_incrementally_idempotent(config, ready):
    cid, vid, root, m, n = ready
    before = n.read_bytes()
    human = m.read_bytes()
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid)
        assert len(job["items"]) == 1
        result = jobs.run(config, db, job)
        assert result["state"] == "completed", result
        assert result["items"][0]["selected"]
        assert n.read_bytes().endswith(before) and m.read_bytes().startswith(human)
        assert '  - "[[Map/热管理 MOC]]"' in n.read_text()
        again = jobs.submit(config, db, cid, vid)
        assert not again["items"], again


def test_pending_scope_uses_current_membership_and_respects_manual_removal(config, ready):
    cid, vid, _, _, _ = ready
    with database.connect(config.state_root) as db:
        first = jobs.run(config, db, jobs.submit(config, db, cid, vid))
        assert first["items"][0]["status"] == "linked"
        assert not jobs.submit(config, db, cid, vid, "待分类")["items"]
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        membership.apply(
            config, db, cid, vid, entities[0], mocs[0], owner="manual", action="unlink"
        )
        pending = jobs.submit(config, db, cid, vid, "待分类")
        assert len(pending["items"]) == 1 and not pending["items"][0]["selected"]
        jobs.run(config, db, pending)
        assert not moc_store.load(config, vid, mocs[0]["id"]).members


def test_manual_reject_and_auto_do_not_add_back(config, ready):
    cid, vid, root, m, n = ready
    with database.connect(config.state_root) as db:
        jobs.run(config, db, jobs.submit(config, db, cid, vid))
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        membership.apply(
            config, db, cid, vid, entities[0], mocs[0], owner="manual", action="unlink"
        )
        new = jobs.run(config, db, jobs.submit(config, db, cid, vid))
        assert not new["items"][0]["selected"]
        assert not moc_store.load(config, vid, mocs[0]["id"]).members


def test_preview_conflict_and_cancel(config, ready):
    cid, vid, _, _, n = ready
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid, preview=True)
        assert job["state"] == "preview"
        n.write_text(n.read_text() + "changed")
        with pytest.raises(IntegrityError):
            jobs.action(config, db, cid, vid, job["id"], "confirm")
        job = jobs.action(config, db, cid, vid, job["id"], "cancel")
        assert jobs.run(config, db, job)["state"] == "cancelled"


def test_automatic_suggest_does_not_write_moc(config, ready):
    cid, vid, _, m, _ = ready
    before = m.read_bytes()
    with database.connect(config.state_root) as db:
        jobs.save_policy(config, db, cid, vid, {"mode": "suggest"}, 0)
        job = jobs.submit(config, db, cid, vid, source="automatic")
        result = jobs.run(config, db, job)
        assert result["state"] == "completed"
        assert m.read_bytes() == before and result["items"][0]["state"] == "suggested"


def test_save_trigger_runs_once_without_model_or_periodic_enable(config, ready):
    from fiber_bundle.organization import store

    cid, vid, _, m, n = ready
    with database.connect(config.state_root) as db:
        jobs.save_policy(config, db, cid, vid, {"mode": "automatic", "periodic": False}, 0)
        jobs.notify(config, vid, "saved", event_id="event-1")
        jobs.notify(config, vid, "saved", event_id="event-1")
    jobs.tick(config)
    jobs.tick(config)
    assert len(store.rows(config, "triggers")) == 1
    assert len(store.rows(config, "jobs")) == 1
    assert store.rows(config, "jobs")[0]["state"] == "completed"
    before = m.read_bytes()
    jobs.tick(config)
    assert m.read_bytes() == before


def test_disabled_policy_does_not_initialize_or_enqueue(config, scope):
    jobs.notify(config, scope[1], "saved", event_id="x")
    assert not (config.state_root / "organization.sqlite3").exists()


def test_permission_epoch_revocation_stops_old_job(config, ready):
    from fiber_bundle import auth

    cid, vid, _, m, _ = ready
    before = m.read_bytes()
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid)
        auth.set_grant(db, cid, vid, query=True, original=True, add=False)
        auth.set_grant(db, cid, vid, query=True, original=True, add=True)
        with pytest.raises(IntegrityError, match="authorization_changed"):
            jobs.run(config, db, job)
    assert m.read_bytes() == before


def test_automatic_policy_change_stops_queued_write(config, ready):
    cid, vid, _, m, _ = ready
    before = m.read_bytes()
    with database.connect(config.state_root) as db:
        jobs.save_policy(config, db, cid, vid, {"mode": "automatic"}, 0)
        job = jobs.submit(config, db, cid, vid, source="automatic")
        jobs.save_policy(config, db, cid, vid, {"mode": "off"}, 1)
        with pytest.raises(IntegrityError, match="policy_changed"):
            jobs.run(config, db, job)
    assert m.read_bytes() == before


def test_inbox_scope_prunes_other_note_reads_before_loading(config, ready, monkeypatch):
    from fiber_bundle import note_index

    cid, vid, root, _, _ = ready
    outside = root / "Resource/private.md"
    outside.write_text("# Outside\n#热管理\n")
    seen = []
    original = note_index.read_note

    def read(root, path, limit):
        seen.append(path)
        return original(root, path, limit)

    monkeypatch.setattr(note_index, "read_note", read)
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid, "Inbox")
        assert len(job["items"]) == 1
    assert "Resource/private.md" not in seen


def test_manual_membership_schedules_index_refresh(config, ready):
    from fiber_bundle.organization import store

    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        membership.apply(config, db, cid, vid, entities[0], mocs[0], owner="manual")
        index = store.get(config, "jobs", "index:" + vid + ":" + cid)
        assert index["index_state"] == "pending"
    jobs.tick(config)
    assert store.get(config, "jobs", index["id"])["index_state"] == "ready"
