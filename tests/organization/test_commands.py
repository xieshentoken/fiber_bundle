import json

import pytest
from test_jobs import ready as ready

from fiber_bundle import database, feishu_events, feishu_receiver, moc_store
from fiber_bundle.organization import commands, jobs, packages, store
from fiber_bundle.weixin_events import Binding


def message(binding, text, event="org-event"):
    payload = {
        "header": {"app_id": "org-app"},
        "event": {
            "sender": {"sender_type": "user", "sender_id": {"open_id": binding.owner_id}},
            "message": {
                "message_id": event,
                "chat_id": "chat-test",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": text}),
            },
        },
    }
    return payload


def test_direct_command_is_not_material_and_duplicate_is_one_job(config, ready):
    cid, vid, _, m, n = ready
    b = Binding("feishu:org-app", "owner", cid, vid)
    feishu_receiver.initialize(config, b)
    payload = message(b, "/归类")
    assert feishu_receiver.capture(config, b, payload)["state"] == "organization_command_recorded"
    assert feishu_receiver.capture(config, b, payload)["state"] == "organization_command_recorded"
    commands.advance(config, b)
    assert len(store.rows(config, "commands")) == 1 and len(store.rows(config, "jobs")) == 1
    jobs.tick(config)
    assert store.rows(config, "jobs")[0]["state"] == "completed"


def test_forwarded_text_and_non_owner_do_not_classify(config, ready):
    cid, vid, *_ = ready
    b = Binding("feishu:org-app", "owner", cid, vid)
    normalized = feishu_events.normalize(b, message(b, "/归类"), commands=True)
    normalized["forwarded"] = True
    assert not commands.capture(config, b, normalized)
    normalized["forwarded"] = False
    normalized["from_user_id"] = "other"
    assert not commands.capture(config, b, normalized)
    assert not store.rows(config, "jobs")


def test_preview_confirm_and_manual_pin(config, ready):
    cid, vid, *_ = ready
    b = Binding("feishu:org-app", "owner", cid, vid)
    with database.connect(config.state_root) as db:
        jid, _ = commands.execute(config, db, b, commands.parse("/归类 预览 Inbox"))
        assert jobs.get(config, db, cid, vid, jid)["state"] == "preview"
        commands.execute(config, db, b, commands.parse("/归类 确认 " + jid))
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        _, body = commands.execute(
            config, db, b, commands.parse("/归类 " + entities[0]["id"] + " 到 MOC:" + mocs[0]["id"])
        )
        assert "人工归类" in body
        assert store.rows(config, "decisions")[0]["state"] == "pinned"


def test_global_scope_requires_preview(config, ready):
    from fiber_bundle.config import InvalidInput

    cid, vid, *_ = ready
    b = Binding("feishu:org-app", "owner", cid, vid)
    with database.connect(config.state_root) as db, pytest.raises(InvalidInput):
        commands.execute(config, db, b, commands.parse("/归类 全库"))


def test_single_command_classifies_pending_scope_and_coalesces_repeated_requests(
    config, ready, package
):
    cid, vid, root, moc, note = ready
    human = moc.read_bytes()
    extra = root / "Resource/待处理.md"
    extra.write_text("# 待处理\n\n#热管理\n热流密度。\n")
    existing = root / "Resource/已分类.md"
    existing.write_text("# 已分类\n\n[[热管理 MOC]]\n")
    unknown = root / "Resource/未明确主题.md"
    unknown.write_text("# 未明确主题\n\n零散待整理记录。\n")
    preserved = {p: p.read_bytes() for p in [note, extra, existing, unknown]}
    package_bytes = {p: p.read_bytes() for p in package.rglob("*") if p.is_file()}
    b = Binding("feishu:org-app", "owner", cid, vid)
    with database.connect(config.state_root) as db:
        registered = packages.register(config, db, cid, vid, "Book/fake-thermal")
    feishu_receiver.initialize(config, b)
    for event in ["pending-first", "pending-second"]:
        assert (
            feishu_receiver.capture(config, b, message(b, "/归类", event))["state"]
            == "organization_command_recorded"
        )
        commands.advance(config, b)
    requests = store.rows(config, "commands", vid)
    assert len(requests) == 2 and len({r["job_id"] for r in requests}) == 1
    with database.connect(config.state_root) as db:
        job = jobs.get(config, db, cid, vid, requests[0]["job_id"])
        paths = {i["path"] for i in job["items"]}
        assert paths == {
            "Inbox/传热.md",
            "Resource/待处理.md",
            "Resource/未明确主题.md",
            "Book/fake-thermal",
        }
        assert sum(i["entity"] == "expert:" + registered["id"] for i in job["items"]) == 1
        result = jobs.run(config, db, job, batch=100)
        assert result["state"] == "completed"
        assert sum(i.get("changed", 0) for i in result["items"]) == 2
        assert all(
            i["status"] in {"review", "unclassified"}
            for i in result["items"]
            if i["path"] in {unknown.relative_to(root).as_posix(), "Book/fake-thermal"}
        )
        _, mocs, _ = jobs.snapshot(config, db, cid, vid)
        members = moc_store.load(config, vid, mocs[0]["id"]).members
        again, _ = commands.execute(config, db, b, commands.parse("/归类"))
        assert jobs.get(config, db, cid, vid, again)["items"] == []
        assert moc_store.load(config, vid, mocs[0]["id"]).members == members
    for path, raw in preserved.items():
        if path in {note, extra}:
            assert path.read_bytes().endswith(raw)
            assert '  - "[[Map/热管理 MOC]]"' in path.read_text()
        else:
            assert path.read_bytes() == raw
    assert all(p.read_bytes() == raw for p, raw in package_bytes.items())
    assert moc.read_bytes().startswith(human)


def test_single_command_default_and_legacy_target_contract():
    assert commands.parse("/归类") == {"action": "run", "target": "待分类"}
    assert commands.parse("/归类  \n") == {"action": "run", "target": "待分类"}
    assert commands.parse("/归类 专家包:fake-heat 到 MOC:热管理 MOC") == {
        "action": "link",
        "target": "专家包:fake-heat",
        "moc": "热管理 MOC",
    }
