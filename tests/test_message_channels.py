import asyncio
import json
import time

import pytest
from test_query_routing_preparation import routing as routing

from fiber_bundle import auth, database, vault_catalog
from fiber_bundle.channels import configuration, control, dispatch, query, store
from fiber_bundle.config import InvalidInput


@pytest.fixture
def channel(config, store, tmp_path):
    db = database.connect(config.state_root)
    cid = auth.create_client(db, "本人合成测试", False)["id"]
    root = tmp_path / "readonly-source"
    root.mkdir()
    (root / "a.md").write_text("# 青松\n合成资料压力 2.4 MPa。")
    p = vault_catalog.preview(
        config, db, name="readonly", root=str(root), scope={"include": [""], "exclude": []}
    )
    vid = vault_catalog.attach(config, db, p["preview_id"])["vault_id"]
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source": "runtime",
                "dependencies": ["deps"],
                "sha256": {},
                "scanner": "scanner",
                "scanner_sha256": "a" * 64,
            }
        )
    )
    secret = tmp_path / "input-credentials.json"
    secret.write_text(
        json.dumps({"app_id": "cli_synthetic", "app_secret": "synthetic-secret-0000000000000000"})
    )
    data = configuration.Save(
        settings=configuration.Settings(
            name="查询入口",
            platform="feishu",
            app_id="cli_synthetic",
            client_id=cid,
            vault_id=vid,
            runtime_manifest=str(manifest),
            query_limit=2,
            outbound_limit=2,
        ),
        credential_file=str(secret),
    )
    value = configuration.save(config, db, data)
    db.close()
    return value, root


def bind(config, value):
    with store.opened(config) as db:
        db.execute(
            "UPDATE channels SET owner='ou_synthetic',state='running' WHERE id=?", (value["id"],)
        )
        return store.get(db, value["id"])


def test_save_is_masked_private_and_queries_do_not_need_add(config, channel, monkeypatch):
    value, root = channel
    assert "synthetic-secret" not in json.dumps(value)
    assert value["state"] == "unpaired" and value["credential_saved"]
    key = configuration.secret_path(config, value["id"])
    assert key.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(
        query, "submit", lambda *args: pytest.fail("save or original preview executed model")
    )
    db = database.connect(config.state_root)
    response = query.test(config, db, value["id"], "压力", False)["result"]
    assert response["searched_vault_ids"] == [value["settings"]["vault_id"]]
    assert response["model_used"] is False and "2.4 MPa" in response["sources"][0]["snippet"]
    with pytest.raises(auth.Denied):
        auth.grant(db, value["settings"]["client_id"], value["settings"]["vault_id"], "add")
    db.close()


def test_identity_dedup_budget_and_actual_evidence_delivery(config, channel):
    value, _ = channel
    binding = bind(config, value)
    assert not dispatch.capture(config, value["id"], 1, "foreign", "ou_other", "查询本库：压力")
    assert dispatch.capture(config, value["id"], 1, "one", "ou_synthetic", "查询本库：压力")
    assert not dispatch.capture(config, value["id"], 1, "one", "ou_synthetic", "查询本库：压力")
    assert dispatch.capture(config, value["id"], 1, "two", "ou_synthetic", "查询本库：压力")
    assert not dispatch.capture(config, value["id"], 1, "three", "ou_synthetic", "查询本库：压力")
    dispatch.advance(config, value["id"], 1)
    sent = []

    async def sender(owner, body, context, guard):
        guard()
        sent.append((owner, body))
        return True

    assert asyncio.run(dispatch.deliver(config, value["id"], 1, sender)) == "sent"
    assert (
        sent[0][0] == binding["owner"]
        and "合成资料压力 2.4 MPa" in sent[0][1]
        and "2.4 MPa" in sent[0][1]
    )
    assert asyncio.run(dispatch.deliver(config, value["id"], 1, sender)) == "idle"
    assert len(sent) == 1


def test_source_change_revocation_and_unlink_prevent_delivery(config, channel):
    value, root = channel
    bind(config, value)
    dispatch.capture(config, value["id"], 1, "one", "ou_synthetic", "查询本库：压力")
    dispatch.advance(config, value["id"], 1)
    (root / "a.md").write_text("# changed\nnew contents")

    async def forbidden(*args):
        pytest.fail("stale evidence sent")

    with pytest.raises(InvalidInput):
        asyncio.run(dispatch.deliver(config, value["id"], 1, forbidden))
    db = database.connect(config.state_root)
    vault_catalog.unlink(config, db, value["settings"]["vault_id"])
    with store.opened(config) as channels:
        assert store.get(channels, value["id"])["state"] == "paused"
    with pytest.raises(InvalidInput):
        dispatch.advance(config, value["id"], 1)
    db.close()


def test_unknown_send_stops_without_replay_and_cooperative_pause(config, channel):
    value, _ = channel
    bind(config, value)
    dispatch.capture(config, value["id"], 1, "one", "ou_synthetic", "查询本库：压力")
    dispatch.advance(config, value["id"], 1)
    calls = []

    async def ambiguous(owner, body, context, guard):
        guard()
        calls.append(body)
        raise TimeoutError()

    assert asyncio.run(dispatch.deliver(config, value["id"], 1, ambiguous)) == "unknown"
    with pytest.raises(InvalidInput, match="reconciliation"):
        asyncio.run(dispatch.deliver(config, value["id"], 1, ambiguous))
    assert len(calls) == 1
    db = database.connect(config.state_root)
    control.pause(config, db, value["id"])
    with store.opened(config) as channels:
        assert store.get(channels, value["id"])["state"] == "paused"
    db.close()


def test_pair_requires_exact_fresh_candidate_and_does_not_start(config, channel):
    value, _ = channel
    with store.opened(config) as db:
        db.execute(
            "UPDATE channels SET candidate=? WHERE id=?",
            (
                json.dumps({"owner_id": "ou_synthetic", "expires_at": time.time() + 600}),
                value["id"],
            ),
        )
    with pytest.raises(InvalidInput):
        control.confirm_owner(config, value["id"], 1, "ou_other")
    result = control.confirm_owner(config, value["id"], 1, "ou_synthetic")
    assert result["state"] == "paused" and result["owner_id"] == "ou_synthetic"
    assert not result["runtime"]["available"]


def test_channel_backup_excludes_credentials_and_restored_binding_stays_paused(
    config, channel, tmp_path
):
    from fiber_bundle import backup_bundle, backup_preflight, backup_restore
    from fiber_bundle.config import load_config

    value, _ = channel
    home = store.home(config, value["id"])
    (home / "fake-token.json").write_text("SYNTHETIC_EXCLUDED_TOKEN")
    result = backup_preflight.inspect(config)
    assert result["state"] == "PASS"
    assert not any(
        "hermes-feishu-gui-" in item["path"] or "message-channel-secrets" in item["path"]
        for item in result["entries"]
    )
    assert any(item["path"] == "message-channels.sqlite3" for item in result["entries"])
    backup_bundle.create(config, tmp_path / "backup", "synthetic-password")
    backup_restore.restore(tmp_path / "backup", tmp_path / "restored", "synthetic-password")
    target = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    with store.opened(target) as db:
        restored = store.get(db, value["id"])
        assert restored["state"] == "paused"
    assert not configuration.secret_path(target, value["id"]).exists()


def test_model_admission_reuses_policy_and_pause_cancels_it(config, channel, routing, monkeypatch):
    from fiber_bundle import query_job_launch, query_policy_store

    value, _ = channel
    main = database.connect(config.state_root)
    policy = query_policy_store.save(config, routing, None)
    with store.opened(config) as db:
        candidate = store.get(db, value["id"])
        settings = candidate["value"] | {
            "answer_mode": "local",
            "configuration_sha256": policy["configuration_sha256"],
            "model_limit": 1,
        }
        changed = store.save(config, main, settings, value["id"], 1)
    bind(config, configuration.public(config, changed))
    launched = []
    monkeypatch.setattr(query_job_launch, "launch", lambda config, cid, jid: launched.append(jid))
    assert dispatch.capture(config, value["id"], 2, "model", "ou_synthetic", "查询本库：压力")
    dispatch.advance(config, value["id"], 2)
    assert len(launched) == 1
    task = main.execute("SELECT * FROM local_query_tasks").fetchone()
    assert task["vault_id"] == value["settings"]["vault_id"]
    assert json.loads(task["request_json"])["mode"] == "cited_draft"
    assert main.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 0
    control.pause(config, main, value["id"])
    assert (
        main.execute("SELECT state FROM query_jobs WHERE id=?", (launched[0],)).fetchone()[0]
        == "cancelled"
    )
    main.close()


def test_paused_channel_cannot_commit_late_archival_receipt(config, channel):
    from fiber_bundle import weixin_inbox
    from fiber_bundle.weixin_events import Binding

    value, _ = channel
    main = database.connect(config.state_root)
    main.execute("UPDATE vaults SET read_only=0 WHERE id=?", (value["settings"]["vault_id"],))
    with store.opened(config) as db:
        settings = store.get(db, value["id"])["value"] | {"add": True}
        changed = store.save(config, main, settings, value["id"], 1)
    bind(config, configuration.public(config, changed))
    binding = Binding(
        "synthetic_archive", "ou_synthetic", settings["client_id"], settings["vault_id"]
    )
    weixin_inbox.initialize(config, binding)
    control.pause(config, main, value["id"])
    with pytest.raises(InvalidInput, match="withdrawn"):
        weixin_inbox.receive(
            config,
            binding,
            "",
            {"msgs": [], "get_updates_buf": "late"},
            guard=lambda: dispatch.archival_guard(config, value["id"], 2),
        )
    assert weixin_inbox.cursor(config, binding) == ""
    with weixin_inbox.connection(config, binding) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    main.close()


def test_gui_model_preview_consumes_budget_once_and_pause_cancels(
    config, channel, routing, monkeypatch
):
    from fiber_bundle import query_job_launch, query_policy_store

    value, _ = channel
    main = database.connect(config.state_root)
    policy = query_policy_store.save(config, routing, None)
    with store.opened(config) as db:
        settings = store.get(db, value["id"])["value"] | {
            "answer_mode": "local",
            "configuration_sha256": policy["configuration_sha256"],
            "model_limit": 1,
        }
        store.save(config, main, settings, value["id"], 1)
    launched = []
    monkeypatch.setattr(query_job_launch, "launch", lambda config, cid, jid: launched.append(jid))
    response = query.test(config, main, value["id"], "压力", True)
    assert launched == [response["job_id"]]
    with pytest.raises(InvalidInput, match="budget"):
        query.test(config, main, value["id"], "压力", True)
    control.pause(config, main, value["id"])
    assert (
        main.execute("SELECT state FROM query_jobs WHERE id=?", (response["job_id"],)).fetchone()[0]
        == "cancelled"
    )
    assert main.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 0
    main.close()
