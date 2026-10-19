from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from test_jobs import ready as ready

from fiber_bundle import database
from fiber_bundle.organization import jobs, model_review, processing, semantic, store

pytest_plugins = ["test_execution"]


def prepare_note(config, ready):
    cid, vid, root, moc, note = ready
    note.write_text(
        "# 无标签资料\n傅里叶定律用于描述固体导热过程。\n热流密度与导热系数和温度梯度相关。\n"
    )
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid, "待分类", source="command")
    return job


def test_model_io_is_outside_lock_and_writes_grounded_result(config, ready, monkeypatch):
    monkeypatch.setattr(model_review, "route", lambda *a: SimpleNamespace(digest="same"))
    job = prepare_note(config, ready)
    monkeypatch.setattr(semantic, "recall", lambda *a: {"state": "disabled"})

    def resolve(config, job, e, mocs, item, check):
        def unlocked():
            with database.lock_wait(0.1), database.locked(config):
                return True

        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(unlocked).result(timeout=2)
        check()
        return {"selected": [mocs[0]["id"]], "status": "linked", "model_called": True}

    monkeypatch.setattr(model_review, "resolve", resolve)
    processing.prepare(config, job["id"])
    with database.locked(config), database.connect(config.state_root) as db:
        current = store.get(config, "jobs", job["id"])
        assert current["items"][0]["evaluated"] and current["model_called"]
        done = jobs.run(config, db, current)
        assert done["state"] == "completed" and done["items"][0]["changed"] == 1


def test_cancel_during_model_does_not_apply_late_result(config, ready, monkeypatch):
    monkeypatch.setattr(model_review, "route", lambda *a: SimpleNamespace(digest="same"))
    job = prepare_note(config, ready)
    before = ready[3].read_bytes()
    monkeypatch.setattr(semantic, "recall", lambda *a: {"state": "disabled"})

    def resolve(config, job, e, mocs, item, check):
        with database.locked(config), database.connect(config.state_root) as db:
            jobs.action(config, db, job["client_id"], job["vault_id"], job["id"], "cancel")
        return {"selected": [mocs[0]["id"]], "status": "linked", "model_called": True}

    monkeypatch.setattr(model_review, "resolve", resolve)
    processing.prepare(config, job["id"])
    assert store.get(config, "jobs", job["id"])["state"] == "cancelled"
    assert ready[3].read_bytes() == before


def test_unconfigured_model_is_paused_not_human_review_and_command_can_resume(
    config, ready, monkeypatch
):
    from fiber_bundle.organization import commands
    from fiber_bundle.weixin_events import Binding

    job = prepare_note(config, ready)
    before = ready[3].read_bytes()
    processing.prepare(config, job["id"])
    with database.connect(config.state_root) as db:
        current = jobs.run(config, db, store.get(config, "jobs", job["id"]))
        assert current["state"] == "partial"
        assert (
            current["items"][0]["state"] == "blocked" and current["items"][0]["status"] == "blocked"
        )
        message = commands.response(current)
        assert "待复核 0" in message and "暂停 1" in message
        key, _ = commands.execute(
            config, db, Binding("feishu:test", "owner", ready[0], ready[1]), commands.parse("/归类")
        )
        assert key == job["id"] and store.get(config, "jobs", key)["state"] == "queued"
    assert ready[3].read_bytes() == before


def test_old_plan_never_silently_executes_new_classifier(config, ready):
    job = prepare_note(config, ready)
    job["organizer_version"] = "fiber-v1"
    store.put(config, "jobs", job["id"], job["vault_id"], job)
    processing.prepare(config, job["id"])
    assert (
        store.get(config, "jobs", job["id"])["error"] == "organization_legacy_plan_replan_required"
    )


def test_real_processing_bridge_accounts_once_and_applies_native_membership(
    config, ready, policy, monkeypatch
):
    import json

    from test_model_ledger import configured

    from fiber_bundle import model_budgets, model_gateway

    selected = configured(config, policy)
    monkeypatch.setattr(model_review, "route", lambda *a: selected)
    monkeypatch.setattr(semantic, "recall", lambda *a: {"state": "disabled"})
    ready[3].write_text("# 热管理 MOC\n## 传热基础与仿真\n人工内容保留。\n")
    before = ready[3].read_bytes()

    def complete(policy, identity, payload):
        data = json.loads(payload["messages"][-1]["content"])
        mid = data["mocs"][0]["id"]
        answer = {
            "moc_id": mid,
            "evidence": ["傅里叶定律用于描述固体导热过程。", "热流密度与导热系数和温度梯度相关。"],
            "scope_quote": "传热基础与仿真",
            "reason": "正文是传热基础。",
        }
        return {
            "usage": {"prompt_tokens": 41, "completion_tokens": 23},
            "choices": [{"message": {"content": json.dumps(answer)}}],
        }

    monkeypatch.setattr(model_gateway, "complete", complete)
    job = prepare_note(config, ready)
    processing.prepare(config, job["id"])
    with database.locked(config), database.connect(config.state_root) as db:
        current = store.get(config, "jobs", job["id"])
        assert current["items"][0]["evaluated"], current
        done = jobs.run(config, db, current)
        assert done["state"] == "completed" and done["items"][0]["changed"] == 1
        assert model_budgets.inspect(db)[0]["used"] == 64
        assert db.execute("SELECT count(*) FROM model_configuration_calls").fetchone()[0] == 1
        assert not jobs.submit(config, db, ready[0], ready[1], "待分类")["items"]
    assert ready[3].read_bytes().startswith(before)


def test_new_command_after_model_change_creates_fresh_plan_and_keeps_old_job(
    config, ready, monkeypatch
):
    from fiber_bundle.organization import commands
    from fiber_bundle.weixin_events import Binding

    job = prepare_note(config, ready)
    processing.prepare(config, job["id"])
    with database.connect(config.state_root) as db:
        old = jobs.run(config, db, store.get(config, "jobs", job["id"]))
        assert old["state"] == "partial"
        monkeypatch.setattr(
            model_review, "route", lambda *a: SimpleNamespace(digest="configured-now")
        )
        identity, _ = commands.execute(
            config, db, Binding("feishu:test", "owner", ready[0], ready[1]), commands.parse("/归类")
        )
        assert identity != job["id"]
        assert store.get(config, "jobs", job["id"]) == old
        fresh = store.get(config, "jobs", identity)
        assert fresh["context"]["model_policy"] == "configured-now" and len(fresh["items"]) == 1


def test_same_command_resumes_failed_writes_and_counts_only_completed_items(config, ready):
    from fiber_bundle.organization import commands
    from fiber_bundle.weixin_events import Binding

    job = prepare_note(config, ready)
    job["state"] = "partial"
    job["completed"] = 1
    job["items"][0].update(state="failed", error="placement_active_policy_required")
    store.put(config, "jobs", job["id"], job["vault_id"], job)
    with database.connect(config.state_root) as db:
        identity, _ = commands.execute(
            config, db, Binding("feishu:test", "owner", ready[0], ready[1]), commands.parse("/归类")
        )
        resumed = store.get(config, "jobs", identity)
        assert identity == job["id"] and resumed["state"] == "queued"
        assert resumed["completed"] == 0 and resumed["items"][0]["state"] == "pending"
        assert not resumed["items"][0].get("error")
