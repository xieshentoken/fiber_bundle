import base64
import copy
import json
import time
from dataclasses import replace
from uuid import uuid4

import pytest
from test_jobs import ready as ready
from test_maintenance import preview
from test_maintenance_apply import execute

from fiber_bundle import (
    database,
    moc_file_journal,
    moc_files,
    moc_registration,
    moc_store,
    moc_write_scope,
    note_index,
    publication_file_io,
    publication_locations,
    vault_scope,
)
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.organization import (
    cached_writeback,
    jobs,
    manual_membership,
    membership,
    model_review,
    note_moc_sync,
    packages,
    processing,
    store,
)
from fiber_bundle.vault_sync import scanner as sync_scanner
from fiber_bundle.vault_sync import store as sync_store

pytest_plugins = ["test_execution"]


def replaced(config, db, ready, path="Inbox/其他文献.md"):
    _, vid, root, _, _ = ready
    file = root / path
    file.write_text("# 旧文献\n保留原件和替换后的人工内容。\n")
    raw, physical = publication_file_io.read(root, path)
    identity = str(uuid4())
    policy = json.loads(
        db.execute("SELECT value FROM vault_save_policies WHERE vault_id=?", (vid,)).fetchone()[0]
    )
    plan = {
        "layout_version": 3,
        "mapping": {"note.md": path},
        "body_files": ["note.md"],
        "outputs": {path: base64.b64encode(raw).decode()},
        "published_hashes": {path: digest(raw)},
        "file_keys": {path: physical},
        "claims": {path: physical[:2]},
        "policy_binding": policy,
    }
    db.execute(
        "INSERT INTO publication_locations VALUES('intake',?,?,?,NULL,1,'published',?)",
        (identity, vid, path, canonical(plan)),
    )
    other = file.with_suffix(".replacement")
    other.write_bytes(raw)
    other.replace(file)
    return publication_locations.note_id("intake", identity), path, file.read_bytes()


@pytest.mark.parametrize("kind", ["note", "expert"])
def test_write_reads_only_related_bodies_and_preserves_unrelated_replacement(
    config, ready, package, monkeypatch, kind
):
    cid, vid, root, moc, _ = ready
    with database.connect(config.state_root) as db:
        if kind == "expert":
            packages.register(config, db, cid, vid, "Book/fake-thermal")
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        entity = next(e for e in entities if e["kind"] == kind)
        _, bad, bad_raw = replaced(config, db, ready)
        seen = []
        original = note_index.read_note

        def read(root, path, limit, **kwargs):
            assert path != bad, "unrelated body entered scoped write"
            seen.append(path)
            return original(root, path, limit, **kwargs)

        monkeypatch.setattr(note_index, "read_note", read)
        membership.apply(config, db, cid, vid, entity, mocs[0], owner="manual")
        assert entity["id"] in moc_store.load(config, vid, mocs[0]["id"]).members
        assert "人工内容保留" in moc.read_text()
        assert (root / bad).read_bytes() == bad_raw
        assert "Map/热管理 MOC.md" in seen
        head = moc_file_journal.latest(moc_file_journal.plans(config, vid, mocs[0]["id"]))
        assert head.value["read_scope"]["members"] == [entity["id"]]


def test_related_replacement_still_blocks_before_committing_another_member(config, ready):
    cid, vid, _, moc, _ = ready
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        entity, target = entities[0], mocs[0]
        membership.apply(config, db, cid, vid, entity, target)
        bad, _, _ = replaced(config, db, ready)
        current = moc_store.load(config, vid, target["id"])
        altered = replace(
            current, members={**current.members, bad: ["system"]}, revision=current.revision + 1
        )
        moc_store.save(config, vid, target["id"], cid, altered, "fixture", current.revision)
        raw = moc.read_bytes()
        with pytest.raises(IntegrityError, match="layout_rebind_required"):
            membership.apply(config, db, cid, vid, entity, target, owner="manual")
        assert moc_store.load(config, vid, target["id"]) == altered
        assert moc.read_bytes() == raw


def test_existing_expert_member_is_revalidated_before_adding_note(config, ready, package):
    cid, vid, _, moc, _ = ready
    with database.connect(config.state_root) as db:
        packages.register(config, db, cid, vid, "Book/fake-thermal")
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        expert = next(e for e in entities if e["kind"] == "expert")
        note = next(e for e in entities if e["kind"] == "note")
        target = mocs[0]
        membership.apply(config, db, cid, vid, expert, target)
        current = moc_store.load(config, vid, target["id"])
        raw = moc.read_bytes()
        catalog = package / packages.CATALOG
        catalog.write_bytes(catalog.read_bytes() + b"\n")
        with pytest.raises(IntegrityError, match="knowledge_module_changed"):
            membership.apply(config, db, cid, vid, note, target, owner="manual")
        assert moc_store.load(config, vid, target["id"]) == current
        assert moc.read_bytes() == raw


def test_new_manual_mount_is_not_blocked_by_unrelated_bad_registration(config, ready):
    cid, vid, _, _, _ = ready
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        replaced(config, db, ready)
        result = manual_membership.apply(
            config, db, cid, vid, entities[0]["id"], [mocs[0]["id"]], "link"
        )
        assert result["completed"], result


def test_new_moc_adoption_and_mount_preserve_bad_note_and_human_topic(config, ready):
    cid, vid, root, _, _ = ready
    with database.connect(config.state_root) as db:
        _, bad, before = replaced(config, db, ready)
        job = preview(
            config,
            db,
            cid,
            vid,
            "additive",
            new_mocs=[
                {"title": "新增材料专题", "topic": "半导体封装材料和物理性能，保留人工主题定义。"}
            ],
        )
        done = execute(config, db, job)
        assert done["state"] == "completed", done
        path = root / job["new_mocs"][0]["path"]
        assert "保留人工主题定义" in path.read_text()
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid, partial=True)
        result = manual_membership.apply(
            config,
            db,
            cid,
            vid,
            next(e for e in entities if e["path"] == "Inbox/传热.md")["id"],
            [next(m for m in mocs if m["path"] == job["new_mocs"][0]["path"])["id"]],
            "link",
        )
        assert result["completed"], result
        assert (root / bad).read_bytes() == before


def test_alias_candidate_appearing_after_plan_invalidates_scoped_journal(config, ready):
    cid, vid, root, moc, _ = ready
    moc.write_text("---\ntype: moc\n---\n# 热管理 MOC\n\n人工链接 [[传热]]。\n")
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        source, target = entities[0], mocs[0]
        note_moc_sync.track(config, db, cid, vid, source)
        info = root.stat()
        record = moc_store.Record(
            target["title"], info.st_dev, info.st_ino, 1, {source["id"]: ["system"]}
        )
        moc_store.save(config, vid, target["id"], cid, record, "fixture", 0)
        raw = moc.read_bytes()
        packet = moc_files.plan_file(
            config,
            db,
            cid,
            vid,
            target["id"],
            1,
            target["path"],
            digest(raw),
            read_scope=moc_write_scope.intent(target["path"], record, raw, target["id"]),
        )
        (root / "Resource/传热.md").write_text("# 同名资料\n不能将短链接误判为唯一。\n")
        with pytest.raises(IntegrityError, match="sources changed"):
            moc_files.apply_file(config, db, cid, vid, target["id"], packet["plan_id"])
        assert moc.read_bytes() == raw


def test_failed_write_and_index_resume_once_uses_local_decision_without_model(
    config, ready, monkeypatch
):
    cid, vid, _, moc, _ = ready
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid)
        job.update(
            state="partial", index_state="failed", index_error="note_index_coverage_incomplete"
        )
        job["items"][0].update(state="failed", error="layout_rebind_required")
        store.put(config, "jobs", job["id"], vid, job)
        assert cached_writeback.available(config, job)
        monkeypatch.setattr(model_review, "resolve", lambda *a, **k: pytest.fail("must not model"))
        resumed = jobs.action(config, db, cid, vid, job["id"], "resume")
        assert resumed["state"] == "queued" and resumed["items"][0]["state"] == "pending"
        assert resumed["resume_mode"] == "cached_writeback"
        processing.prepare(config, job["id"])
        done = jobs.run(config, db, store.get(config, "jobs", job["id"]))
        assert done["state"] == "completed" and done["items"][0]["changed"] == 1, done
        before = moc.read_bytes()
        jobs.run(config, db, done)
        assert moc.read_bytes() == before
        with pytest.raises(InvalidInput, match="not_resumable"):
            jobs.action(config, db, cid, vid, job["id"], "resume")


def test_cached_only_prepare_rejects_missing_completed_decision(config, ready, monkeypatch):
    cid, vid, _, _, note = ready
    note.write_text("# 无标签资料\n这篇尚未完成分析，不能在缓存恢复中启动模型。\n")
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid)
        job["resume_mode"] = "cached_writeback"
        store.put(config, "jobs", job["id"], vid, job)
    monkeypatch.setattr(model_review, "resolve", lambda *a, **k: pytest.fail("no model fallback"))
    processing.prepare(config, job["id"])
    stopped = store.get(config, "jobs", job["id"])
    assert stopped["state"] == "partial"
    assert stopped["error"] == "organization_model_cache_incomplete"


def analyzed_failure(config, ready, policy, monkeypatch):
    from test_model_ledger import configured
    from test_processing import prepare_note

    from fiber_bundle import model_gateway
    from fiber_bundle.organization import semantic

    selected = configured(config, policy)
    monkeypatch.setattr(model_review, "route", lambda *a: selected)
    monkeypatch.setattr(semantic, "recall", lambda *a: {"state": "disabled"})
    ready[3].write_text("# 热管理 MOC\n## 传热基础与仿真\n人工内容保留。\n")

    def complete(policy, identity, payload):
        data = json.loads(payload["messages"][-1]["content"])
        reply = {
            "moc_id": data["mocs"][0]["id"],
            "evidence": ["傅里叶定律用于描述固体导热过程。", "热流密度与导热系数和温度梯度相关。"],
            "scope_quote": "传热基础与仿真",
            "reason": "正文是传热基础。",
        }
        return {
            "usage": {"prompt_tokens": 41, "completion_tokens": 23},
            "choices": [{"message": {"content": json.dumps(reply)}}],
        }

    monkeypatch.setattr(model_gateway, "complete", complete)
    job = prepare_note(config, ready)
    processing.prepare(config, job["id"])
    original = membership.apply
    monkeypatch.setattr(
        membership,
        "apply",
        lambda *a, **k: (_ for _ in ()).throw(IntegrityError("layout_rebind_required")),
    )
    with database.connect(config.state_root) as db:
        failed = jobs.run(config, db, store.get(config, "jobs", job["id"]))
        assert failed["state"] == "partial" and failed["items"][0]["model_used"]
        failed.update(index_state="failed", index_error="note_index_coverage_incomplete")
        store.put(config, "jobs", job["id"], ready[1], failed)
    monkeypatch.setattr(membership, "apply", original)
    monkeypatch.setattr(model_gateway, "complete", lambda *a, **k: pytest.fail("must not send"))
    monkeypatch.setattr(model_review, "call", lambda *a, **k: pytest.fail("must use cache"))
    return failed


def test_validated_cached_model_results_resume_write_without_call_or_cache_changes(
    config, ready, policy, monkeypatch
):
    failed = analyzed_failure(config, ready, policy, monkeypatch)
    before = copy.deepcopy(store.rows(config, "model_reviews", ready[1]))
    assert cached_writeback.available(config, failed)
    with database.connect(config.state_root) as db:
        calls = list(db.execute("SELECT * FROM model_configuration_calls"))
        resumed = jobs.action(config, db, ready[0], ready[1], failed["id"], "resume")
        processing.prepare(config, failed["id"])
        done = jobs.run(config, db, resumed)
        assert done["state"] == "completed", done
        assert list(db.execute("SELECT * FROM model_configuration_calls")) == calls
    assert store.rows(config, "model_reviews", ready[1]) == before


def test_missing_cache_stops_recovery_without_falling_back_to_model(
    config, ready, policy, monkeypatch
):
    failed = analyzed_failure(config, ready, policy, monkeypatch)
    before = ready[3].read_bytes()
    with store.connection(config, write=True) as side:
        side.execute("DELETE FROM model_reviews")
    assert not cached_writeback.available(config, failed)
    with database.connect(config.state_root) as db:
        resumed = jobs.action(config, db, ready[0], ready[1], failed["id"], "resume")
        done = jobs.run(config, db, resumed)
    assert done["state"] == "partial", done
    assert done["items"][0]["error"] == "organization_model_cache_incomplete"
    assert ready[3].read_bytes() == before


def test_legacy_completed_coverage_resume_finishes_committed_membership_once(
    config, ready, monkeypatch
):
    cid, vid, _, moc, note = ready
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid)
        entities, mocs, context = jobs.snapshot(config, db, cid, vid, partial=True)
        entity, target = entities[0], mocs[0]
        # The old task predates the display-only complete coverage field.
        job["context"] = {k: v for k, v in context.items() if k != "coverage"}
        job["items"][0]["fingerprint"] = jobs.current_plan(job, entity, mocs, [], context)[
            "fingerprint"
        ]
        note_moc_sync.track(config, db, cid, vid, entity)
        raw = moc.read_bytes()
        packet = moc_registration.preview(
            config,
            db,
            cid,
            vid,
            target["path"],
            digest(raw),
            read_scope=moc_write_scope.intent(target["path"], None, raw, target["id"]),
        )
        moc_registration.apply(config, db, cid, vid, packet["operation_id"], "apply")
        current = moc_store.load(config, vid, target["id"])
        committed = replace(
            current, members={entity["id"]: ["system"]}, revision=current.revision + 1
        )
        moc_store.save(config, vid, target["id"], cid, committed, "fixture", current.revision)
        before = moc.read_bytes()
        assert entity["id"] not in note.read_text()
        job["state"] = "partial"
        job["items"][0].update(state="failed", error="layout_rebind_required")
        store.put(config, "jobs", job["id"], vid, job)
        assert processing.unchanged(config, db, job)
        monkeypatch.setattr(model_review, "resolve", lambda *a, **k: pytest.fail("no model"))
        done = jobs.run(config, db, jobs.action(config, db, cid, vid, job["id"], "resume"))
        assert done["state"] == "completed" and done["items"][0]["changed"] == 0, done
        assert moc.read_bytes() != before and "moc:" in note.read_text()
        assert moc_store.load(config, vid, target["id"]) == committed
        after = moc.read_bytes(), note.read_bytes()
        jobs.run(config, db, done)
        assert (moc.read_bytes(), note.read_bytes()) == after


@pytest.mark.parametrize(
    "coverage",
    [
        {"complete": False, "unavailable_count": 0, "unavailable": []},
        {"complete": True, "unavailable_count": False, "unavailable": []},
        {"complete": True, "unavailable_count": 1, "unavailable": []},
        {"complete": True, "unavailable_count": 0, "unavailable": ["one"]},
        {"complete": True, "unavailable_count": 0, "unavailable": [], "extra": True},
    ],
)
def test_legacy_comparison_never_discards_incomplete_or_unrecognized_coverage(coverage):
    context = {"catalog_sha256": "current", "coverage": coverage}
    assert jobs.comparison_context({"context": {"catalog_sha256": "old"}}, context) == context


def test_legacy_complete_coverage_does_not_hide_other_context_changes():
    context = {
        "catalog_sha256": "changed",
        "coverage": {"complete": True, "unavailable_count": 0, "unavailable": []},
    }
    old = {"catalog_sha256": "old"}
    assert jobs.comparison_context({"context": old}, context) == {"catalog_sha256": "changed"}
    assert jobs.comparison_context({"context": old}, context) != old
    assert jobs.comparison_context({"context": context}, context) == context


def test_owned_moc_and_note_exchange_queue_parents_and_restore_catalog(config, ready):
    cid, vid, root, _, _ = ready
    with database.connect(config.state_root) as db:
        from fiber_bundle import auth

        row = auth.grant(db, cid, vid, "query")
        sync_store.register(config, vid, str(root), vault_scope.for_vault(root, row), "fixture")
        while tasks := sync_store.due(config, now=time.time() + 100):
            for task in tasks:
                sync_scanner.apply(config, task, now=time.time())
        sync_scanner.heartbeat(config, vid)
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid, partial=True)
        membership.apply(config, db, cid, vid, entities[0], mocs[0], owner="manual")
        pending = {t["path"]: t["flags"] for t in sync_store.due(config, now=time.time() + 100)}
        assert {"Map", "Map/热管理 MOC.md", "Inbox", "Inbox/传热.md"} <= set(pending)
        assert pending["Map"] == pending["Inbox"] == 0
        # No FSEvents input: only the successful write's durable dirty queue runs.
        while tasks := sync_store.due(config, now=time.time() + 100):
            for task in tasks:
                sync_scanner.apply(config, task, now=time.time())
        _, current, context = jobs.snapshot(config, db, cid, vid, partial=True)
        assert context["coverage"]["complete"]
        assert entities[0]["id"] in current[0]["members"]
