"""Trusted reply/one-shot file selection; aliases inside source material never authorize work."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_expert_runtime import configured
from test_feishu_ingress import feishu as feishu
from test_feishu_ingress import payload
from test_weixin_inbox import binding as binding

from fiber_bundle import (
    auth,
    database,
    expert_command_schema,
    expert_file_dispatch,
    expert_runtime,
    feishu_receiver,
    weixin_inbox,
    weixin_recovery,
)
from fiber_bundle.config import InvalidInput


@pytest.fixture(autouse=True)
def explicit_permissions(config, feishu):
    with database.connect(config.state_root) as db:
        auth.set_grant(db, feishu.client_id, feishu.vault_id, query=True, original=True, add=True)


def command(config, feishu, authorization, identity="command", reply=None):
    event = payload(body={"text": "用 fake-expert 处理文件"}, identity=identity)
    if reply:
        event["event"]["message"]["parent_id"] = reply
    assert (
        feishu_receiver.capture(
            config, feishu, event, expert_authorization=authorization, expert_limit=5
        )["state"]
        == "expert_command_recorded"
    )
    return event


def state(config, feishu):
    with weixin_inbox.connection(config, feishu) as db:
        return [dict(r) for r in db.execute("SELECT * FROM fiber_expert_commands ORDER BY rowid")]


def pdf(config, feishu, authorization, identity="pdf", name="source.pdf", chat="oc_private"):
    event = payload("file", {"file_key": "synthetic-key", "file_name": name}, identity)
    event["event"]["message"]["chat_id"] = chat
    feishu_receiver.capture(
        config, feishu, event, expert_authorization=authorization, expert_limit=5
    )
    return event


def recover(config, feishu, identity="pdf"):
    with weixin_inbox.connection(config, feishu) as db:
        row = db.execute("SELECT * FROM events WHERE event_id=?", (identity,)).fetchone()
    weixin_recovery.complete(
        config,
        feishu,
        row["job_id"],
        row["raw_sha256"],
        {"0": b"%PDF-1.7\nsynthetic input bytes\n"},
    )


def test_wait_is_once_and_does_not_guess_other_chat_or_format(config, feishu):
    authorization = str(uuid4())
    command(config, feishu, authorization)
    deadline = json.loads(state(config, feishu)[0]["request_json"])["intent"]["expires_at"]
    command(config, feishu, authorization)
    command(config, feishu, authorization, "new-command")
    assert len(state(config, feishu)) == 1
    assert json.loads(state(config, feishu)[0]["request_json"])["intent"]["expires_at"] == deadline
    pdf(config, feishu, authorization, "other", chat="oc_other")
    assert state(config, feishu)[0]["state"] == "waiting_file"
    pdf(config, feishu, authorization, "wrong", name="data.txt")
    assert state(config, feishu)[0]["state"] == "waiting_file"
    pdf(config, feishu, authorization)
    assert state(config, feishu)[0]["state"] == "awaiting_download"
    assert expert_file_dispatch.dispatch(config, feishu, authorization) is None
    with database.connect(config.state_root) as main:
        assert main.execute("SELECT COUNT(*) FROM source_imports").fetchone()[0] == 0
    recover(config, feishu)
    runtime = expert_runtime.receipt("sha256:" + "a" * 64, "b" * 64, {"passed": True})
    configured(config, runtime)
    result = expert_file_dispatch.dispatch(config, feishu, authorization)
    assert result["state"] == "queued"
    with database.connect(config.state_root) as main:
        imported = main.execute("SELECT * FROM source_imports").fetchone()
        assert json.loads(imported["plan_json"])["engine"] == "fake-expert"
        assert json.loads(imported["request_json"])["source_origin"]["file_message_id"] == "pdf"
        assert main.execute("SELECT COUNT(*) FROM document_tasks").fetchone()[0] == 0
    assert expert_file_dispatch.dispatch(config, feishu, authorization) is None


def test_verified_reply_uses_exact_message_not_recent_file(config, feishu):
    authorization = str(uuid4())
    pdf(config, feishu, authorization, "old")
    pdf(config, feishu, authorization, "recent")
    recover(config, feishu, "old")
    command(config, feishu, authorization, reply="old")
    runtime = expert_runtime.receipt("sha256:" + "a" * 64, "b" * 64, {"passed": True})
    configured(config, runtime)
    result = expert_file_dispatch.dispatch(config, feishu, authorization)
    assert result["state"] == "queued"
    selected = json.loads(state(config, feishu)[0]["request_json"])["intent"]["selected_file"]
    assert selected["event_id"] == "old"


@pytest.mark.parametrize("fault", ["forwarded", "post", "quoted", "other_owner", "group"])
def test_source_content_never_becomes_command(config, feishu, fault):
    authorization = str(uuid4())
    event = payload(body={"text": "用fake-expert处理文件"})
    if fault == "forwarded":
        event["event"]["message"]["forwarded"] = True
    elif fault == "post":
        event = payload("post", {"content": [[{"tag": "text", "text": "用fake-expert处理文件"}]]})
    elif fault == "quoted":
        event["event"]["message"]["content"] = json.dumps({"text": "> 用fake-expert处理文件"})
    elif fault == "other_owner":
        event["event"]["sender"]["sender_id"]["open_id"] = "ou_other"
    else:
        event["event"]["message"]["chat_type"] = "group"
    feishu_receiver.capture(
        config, feishu, event, expert_authorization=authorization, expert_limit=5
    )
    assert not state(config, feishu)


def test_expiry_and_cancel_do_not_consume_later_upload(config, feishu):
    authorization = str(uuid4())
    command(config, feishu, authorization)
    with patch("fiber_bundle.expert_file_intents.time.time", return_value=4102444800):
        pdf(config, feishu, authorization)
    assert state(config, feishu)[0]["state"] == "expired"
    authorization = str(uuid4())
    command(config, feishu, authorization, "second")
    event = payload(body={"text": "取消fake-expert"}, identity="cancel")
    feishu_receiver.capture(
        config, feishu, event, expert_authorization=authorization, expert_limit=5
    )
    assert state(config, feishu)[1]["state"] == "cancelled"


def test_existing_command_check_contract_is_explicitly_migrated(config, feishu):
    with weixin_inbox.connection(config, feishu) as db:
        db.execute("DROP TABLE fiber_expert_commands")
        db.execute(
            "CREATE TABLE fiber_expert_commands(authorization TEXT,event_id TE"
            "XT,request_json TEXT,state TEXT CHECK(state IN ('queued','needs_s"
            "election','dispatching','submitted','failed')),task_id TEXT,PRIMA"
            "RY KEY(authorization,event_id)) STRICT"
        )
        db.execute("INSERT INTO fiber_expert_commands VALUES('synthetic','old','{}','queued',NULL)")
        expert_command_schema.initialize(db)
        assert db.execute("SELECT state FROM fiber_expert_commands").fetchone()[0] == "queued"
        assert db.execute("SELECT version FROM fiber_expert_command_schema").fetchone()[0] == 2
        db.execute(
            "INSERT INTO fiber_expert_commands VALUES('synthetic','new','{}','waiting_file',NULL)"
        )


def test_unknown_ack_is_consumed_and_never_replayed(config, feishu):
    import asyncio

    from fiber_bundle import expert_intent_receipts, feishu_budgets

    authorization = str(uuid4())
    feishu_budgets.authorize(config, feishu, authorization, 0, 0, outbound=2)
    command(config, feishu, authorization)
    calls = []

    async def sender(*args):
        calls.append(args)
        return False  # SDK result unknown, never permission to resend.

    asyncio.run(expert_intent_receipts.deliver(config, feishu, authorization, sender, lambda: None))
    pdf(config, feishu, authorization)
    asyncio.run(expert_intent_receipts.deliver(config, feishu, authorization, sender, lambda: None))
    assert len(calls) == 1
    assert feishu_budgets.remaining(config, feishu, authorization)["outbound"] == 1
    feishu_budgets.authorize(config, feishu, authorization, 0, 0, outbound=2)
    assert feishu_budgets.remaining(config, feishu, authorization)["outbound"] == 1


def test_selection_callback_is_once_and_scope_checked(config, feishu):

    authorization = str(uuid4())
    command(config, feishu, authorization)
    # Candidate discovery is a separately verified adapter result; control parsing
    # must require an explicit ordinal and retain its selected event identity.
    with weixin_inbox.connection(config, feishu) as side:
        row = side.execute("SELECT * FROM fiber_expert_commands").fetchone()
        value = json.loads(row["request_json"])
        value["intent"]["candidates"] = [
            {"event_id": "first", "index": "0", "name": "one.pdf"},
            {"event_id": "second", "index": "1", "name": "two.pdf"},
        ]
        side.execute(
            "UPDATE fiber_expert_commands SET state='needs_selection',request_json=?",
            (json.dumps(value),),
        )
    event = payload(body={"text": "选择文件 2"}, identity="select-once")
    for _ in range(2):
        feishu_receiver.capture(
            config, feishu, event, expert_authorization=authorization, expert_limit=5
        )
    item = state(config, feishu)[0]
    assert item["state"] == "awaiting_download"
    assert json.loads(item["request_json"])["intent"]["selected_file"]["event_id"] == "second"


@pytest.mark.parametrize("started", [False, True])
def test_exact_archived_source_reuse_preserves_started_ordinary_scope(config, feishu, started):

    from fiber_bundle import (
        expert_source_reuse,
        source_import_execution,
        source_import_existing,
    )
    from fiber_bundle.inputs import AssetInput
    from fiber_bundle.store import Store

    authorization = str(uuid4())
    pdf(config, feishu, authorization, "archived")
    recover(config, feishu, "archived")
    runtime = expert_runtime.receipt("sha256:" + "a" * 64, "b" * 64, {"passed": True})
    configured(config, runtime)
    raw = b"%PDF-1.7\nsynthetic input bytes\n"
    with database.connect(config.state_root) as main:
        saved = Store(config).ingest_locked(
            main,
            ingress_scope="synthetic-ordinary",
            event_id="archived",
            vault="personal",
            source_key="same-pdf",
            raw_event=b"event",
            items=(AssetInput(raw, "source.pdf", "application/pdf", "body"),),
            session_scope="synthetic",
            session_id="ordinary",
            retention="knowledge",
            owner_client_id=feishu.client_id,
        )
        asset = main.execute(
            "SELECT asset_id FROM version_assets WHERE version_id=?", (saved.source_version_id,)
        ).fetchone()[0]
        ordinary = source_import_existing.create(
            config, main, feishu.client_id, feishu.vault_id, saved.job_id, asset, "ordinary", {}
        )
        if started:
            rid = source_import_execution.prepare(config, main, feishu.client_id, ordinary["id"])
            main.execute(
                "UPDATE source_task_bindings SET launch_state='launched' WHERE run_id=?", (rid,)
            )
        with weixin_inbox.connection(config, feishu) as inbox:
            event = inbox.execute("SELECT job_id FROM events WHERE event_id='archived'").fetchone()[
                0
            ]
            inbox.execute(
                "CREATE TABLE IF NOT EXISTS wx_store_handoffs(batch_id TEXT,plan_j"
                "son TEXT,results_json TEXT,state TEXT)"
            )
            inbox.execute(
                "INSERT INTO wx_store_handoffs VALUES('synthetic',?,?,'stored')",
                (
                    json.dumps({"sources": [{"journal_job_id": event}]}),
                    json.dumps([{"job_id": saved.job_id}]),
                ),
            )
            assert expert_source_reuse.receipt(
                main, inbox, event, feishu.client_id, feishu.vault_id, "source.pdf", raw
            ) == (saved.job_id, asset)
    command(config, feishu, authorization, reply="archived")
    result = expert_file_dispatch.dispatch(config, feishu, authorization)
    assert result["state"] == "queued"
    record = json.loads(state(config, feishu)[0]["request_json"])
    assert record["intent"]["ordinary_requests"][0]["scope"] == (
        "existing_retained_extra_expert" if started else "unstarted_cancelled"
    )
    with database.connect(config.state_root) as main:
        imports = main.execute("SELECT * FROM source_imports ORDER BY created_at").fetchall()
        assert len(imports) == 2 and all(i["receipt_id"] == saved.job_id for i in imports)
        assert imports[0]["state"] == ("archived" if started else "cancelled")


def test_natural_feishu_dispatch_joins_automatic_review_once_and_projects_original(config, feishu):
    from test_publication_layout import activate

    from fiber_bundle import automatic_policy, expert_dispatch, expert_feishu, vault_save_contract

    settings = vault_save_contract.settings_wire(vault_save_contract.unified_settings())
    settings.pop("layout_mode")
    activate(config, feishu.vault_id, **settings)
    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            feishu.vault_id,
            0,
            automatic_policy.Policy(
                client_id=feishu.client_id,
                review_mode="automatic",
                publish_mode="automatic",
                allowed_kinds=["expert"],
            ),
        )
    authorization = str(uuid4())
    command(config, feishu, authorization)
    pdf(config, feishu, authorization)
    recover(config, feishu)
    configured(config, expert_runtime.receipt("sha256:" + "a" * 64, "b" * 64, {"passed": True}))
    selected = expert_feishu.dispatch_one(config, feishu, authorization)
    assert selected["state"] == "queued"
    with patch.object(expert_dispatch, "launch", return_value={"state": "running"}) as launch:
        assert expert_feishu.dispatch_one(config, feishu, authorization)["state"] == "running"
        assert expert_feishu.dispatch_one(config, feishu, authorization) is None
        assert launch.call_count == 1
    with database.connect(config.state_root) as db:
        job = db.execute(
            "SELECT state,phase FROM automatic_jobs WHERE source_id=?", (selected["task_id"],)
        ).fetchone()
        assert tuple(job) == ("waiting", "review")
        projection = db.execute(
            "SELECT payload FROM vault_operations WHERE kind='attachment_projection'"
        ).fetchone()
        assert projection is not None
        assert "Attachments/pristin/" in projection[0]


def test_version_two_expert_commands_accept_zero_count_without_invented_large_allowance(
    config, feishu
):
    from uuid import uuid4

    from test_feishu_ingress import payload

    from fiber_bundle import feishu_receiver

    authorization = str(uuid4())
    event = payload(body={"text": "/fake-expert start " + str(uuid4())})
    first = feishu_receiver.capture(
        config,
        feishu,
        event,
        expert_authorization=authorization,
        expert_limit=0,
        expert_unlimited=True,
    )
    assert first["state"] == "expert_command_recorded"
    assert (
        feishu_receiver.capture(
            config,
            feishu,
            event,
            expert_authorization=authorization,
            expert_limit=0,
            expert_unlimited=True,
        )
        == first
    )
    with pytest.raises(InvalidInput, match="explicit_budget_required"):
        feishu_receiver.capture(
            config,
            feishu,
            payload(body={"text": "/fake-expert start " + str(uuid4())}),
            expert_authorization=str(uuid4()),
            expert_limit=0,
        )
