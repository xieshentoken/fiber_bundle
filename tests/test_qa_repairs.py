"""Observable preflight/start/error behavior with synthetic local data only."""

import json

import pytest
from fastapi.testclient import TestClient
from test_message_channels import bind
from test_message_channels import channel as channel

from fiber_bundle import api, auth, database, runtime_environment, vault_files, vault_scope
from fiber_bundle.channels import configuration, control, store, validation
from fiber_bundle.http_guard import Settings


def test_inventory_has_no_total_byte_cap_and_rechecks_files(tmp_path):
    root = tmp_path / "large"
    root.mkdir()
    for i in range(5):
        with (root / f"part-{i}.bin").open("wb") as stream:
            stream.truncate(53 * 1024 * 1024)
    scope = vault_scope.Scope(("",), (), ())
    value = vault_files.inventory(root, scope)
    assert value["bytes"] == 265 * 1024 * 1024
    assert len(value["files"]) == 5
    assert vault_files.inventory(root, scope)["files"] == value["files"]
    with (root / "oversized.bin").open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    with pytest.raises(Exception, match="vault_file_size_limit"):
        vault_files.inventory(root, scope)


def test_child_environment_preserves_only_explicit_helpers(monkeypatch):
    monkeypatch.setenv("FIBER_TRASH_HELPER", "/synthetic/vault-trash")
    monkeypatch.setenv("FIBER_TRASH_HELPER_SHA256", "a" * 64)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-do-not-inherit")
    env = runtime_environment.child_environment()
    assert env["FIBER_TRASH_HELPER"] == "/synthetic/vault-trash"
    assert env["FIBER_TRASH_HELPER_SHA256"] == "a" * 64
    assert "OPENAI_API_KEY" not in env


def test_expert_failures_list_all_missing_fields_before_any_write(config, channel):
    item, _ = channel
    main = database.connect(config.state_root)
    data = configuration.Save(
        settings=configuration.Settings(
            **{
                **item["settings"],
                "expert_enabled": True,
                "original": False,
                "add": False,
                "expert_limit": 0,
            }
        ),
        app_secret="synthetic-secret-0000000000",
    )
    before = [tuple(r) for r in main.execute("SELECT * FROM client_vault_grants")]
    with pytest.raises(validation.ChannelValidationError) as error:
        configuration.save(config, main, data)
    assert error.value.issues == [
        "expert_original_required",
        "expert_add_required",
        "expert_limit_required",
    ]
    assert [tuple(r) for r in main.execute("SELECT * FROM client_vault_grants")] == before
    with store.readonly(config) as side:
        assert side.execute("SELECT count(*) FROM channels").fetchone()[0] == 1
    main.close()


def test_start_readiness_and_error_response_never_launch(config, channel, monkeypatch):
    item, _ = channel
    bind(config, item)
    with store.opened(config) as side:
        row = store.get(side, item["id"])
        row["value"].update(query_limit=0, outbound_limit=0)
        side.execute(
            "UPDATE channels SET state='paused',value=? WHERE id=?",
            (json.dumps(row["value"]), item["id"]),
        )
    origin = "http://127.0.0.1:18965"
    auth.set_password(config, "synthetic-test-password")
    app = api.create_app(config, Settings(port=18965))
    monkeypatch.setattr(
        control.subprocess, "Popen", lambda *a, **k: pytest.fail("invalid start launched process")
    )
    with TestClient(app, base_url=origin, client=("127.0.0.1", 49100)) as http:
        result = http.post(
            "/admin/login", json={"password": "synthetic-test-password"}, headers={"Origin": origin}
        )
        http.headers.update({"Origin": origin, "X-CSRF-Token": result.json()["csrf"]})
        path = f"/admin/channels/{item['id']}"
        ready = http.get(path + "/start-readiness")
        assert ready.status_code == 200
        assert ready.json() == {
            "ready": False,
            "issues": ["query_limit_required", "outbound_limit_required"],
            "revision": 1,
        }
        denied = http.post(path + "/start")
        assert denied.status_code == 422
        assert denied.json() == {
            "error": "channel_configuration_invalid",
            "issues": ["query_limit_required", "outbound_limit_required"],
        }
        assert "synthetic-secret" not in denied.text
    with store.readonly(config) as side:
        assert store.get(side, item["id"])["state"] == "paused"


def test_start_modes_keep_evidence_zero_models_and_archive_exception():
    base = {
        "expert_enabled": False,
        "query_limit": 2,
        "outbound_limit": 2,
        "model_limit": 0,
        "answer_mode": "evidence",
        "add": False,
    }
    assert validation.start_issues(base, intake_enabled=False) == []
    assert validation.start_issues({**base, "answer_mode": "local"}, intake_enabled=False) == [
        "model_limit_required"
    ]
    assert (
        validation.start_issues(
            {**base, "add": True, "query_limit": 0, "outbound_limit": 0}, intake_enabled=True
        )
        == []
    )


def test_worker_diagnostics_do_not_copy_exception_text_or_locals():
    from fiber_bundle.channels.diagnostics import failure

    try:
        raise RuntimeError("synthetic-secret-and-private-message")
    except RuntimeError as error:
        result = failure(error)
    assert result["error"] == "channel_internal_failed"
    assert "synthetic-secret" not in json.dumps(result)
    assert "private-message" not in json.dumps(result)


def test_worker_archives_one_link_without_enabling_extraction_or_stopping(
    config, channel, monkeypatch
):
    import asyncio
    import time

    from test_channel_worker import launch_file
    from test_feishu_ingress import payload

    from fiber_bundle import feishu_receiver
    from fiber_bundle.channels import intake_contract, intake_repository, worker

    item, _ = channel
    bound = bind(config, item)
    main = database.connect(config.state_root)
    vid, cid = bound["value"]["vault_id"], bound["value"]["client_id"]
    main.execute("UPDATE vaults SET read_only=0 WHERE id=?", (vid,))
    auth.set_grant(main, cid, vid, query=True, original=True, add=True)
    main.close()
    with store.opened(config) as side:
        bound["value"].update(add=True, original=True, download_limit=2)
        side.execute(
            "UPDATE channels SET value=? WHERE id=?", (json.dumps(bound["value"]), item["id"])
        )
    intake_repository.save(
        config,
        item["id"],
        intake_contract.Save(
            expected_revision=0,
            settings=intake_contract.Settings(
                enabled=True, target_vault_id=vid, store_limit=3, download_limit=2
            ),
        ),
    )
    launch, _ = launch_file(config, item["id"])

    class Adapter:
        is_connected = True
        _fiber_error = None

        async def connect(self):
            event = payload(body={"text": "https://mp.weixin.qq.com/s/synthetic"})
            event["event"]["sender"]["sender_id"]["open_id"] = bound["owner"]
            feishu_receiver.capture(
                config, configuration.feishu_settings(config, bound).binding, event
            )
            return True

        async def disconnect(self):
            self.is_connected = False

    adapter = Adapter()
    monkeypatch.setattr(worker.feishu, "adapter", lambda *args: adapter)
    monkeypatch.setattr(worker.feishu_gateway_cli, "isolate", lambda *args: None)
    monkeypatch.setattr(worker.feishu_gateway_cli, "bootstrap", lambda *args: None)

    async def exercise():
        task = asyncio.create_task(worker.run(config, launch))
        try:
            deadline = time.monotonic() + 7
            while time.monotonic() < deadline:
                if task.done():
                    await task
                    pytest.fail("worker stopped during link archival")
                with store.readonly(config) as side:
                    row = side.execute(
                        "SELECT state,value FROM channel_intake_records WHERE channel_id=?",
                        (item["id"],),
                    ).fetchone()
                if row and row["state"] == "stored":
                    assert json.loads(row["value"])["extraction"] == "off"
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("link was not archived")
            await asyncio.sleep(1.1)
            assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


def test_invalid_fields_report_names_without_echoing_values(http, channel):
    item, _ = channel
    value = {**item["settings"], "name": "", "query_limit": 101}
    result = http.post(
        "/admin/channels", json={"settings": value, "app_secret": "synthetic-private-secret"}
    )
    assert result.status_code == 422
    assert result.json() == {
        "error": "channel_input_invalid",
        "issues": ["name_invalid", "query_limit_invalid"],
    }
    assert "synthetic-private-secret" not in result.text
