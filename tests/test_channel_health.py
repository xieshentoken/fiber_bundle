"""Status observation uses real leases and databases, without provider or message effects."""

import json
import time

import pytest
from test_message_channels import bind
from test_message_channels import channel as channel

from fiber_bundle import database, feishu_runtime
from fiber_bundle.channels import health, store


def test_overview_does_not_create_sidecar_or_import_hermes(config, store):
    main = database.connect(config.state_root)
    try:
        assert health.overview(config, main)["channels"] == []
        assert not (config.state_root / "message-channels.sqlite3").exists()
    finally:
        main.close()


def test_connected_transport_is_not_query_readiness(config, channel):
    value, _ = channel
    current = bind(config, value)
    main = database.connect(config.state_root)
    try:
        home = store.home(config, value["id"])
        with feishu_runtime.Instance(home) as instance:
            instance.status("connected", {})
            feishu_runtime.write(
                home / "channel-status.json",
                {
                    "instance_id": instance.identity,
                    "revision": 1,
                    "nonce": "synthetic",
                    "state": "query_ready",
                    "platform": "feishu",
                },
            )
            result = health.present(config, main, current)
            assert result["runtime"]["process_state"] == "running"
            assert result["transport"] == "connected"
            assert result["capabilities"]["query"]["available"]
            with store.opened(config) as side:
                side.execute("UPDATE channels SET state='paused' WHERE id=?", (value["id"],))
                current = store.get(side, value["id"])
            result = health.present(config, main, current)
            assert result["transport"] == "connected"
            assert not result["capabilities"]["query"]["available"]
            assert result["capabilities"]["query"]["reason"] == "channel_paused"
        result = health.present(config, main, current)
        assert result["runtime"]["process_state"] == "stopped"
        assert result["transport"] == "disconnected"
    finally:
        main.close()


def test_stale_heartbeat_keeps_process_evidence_without_green_transport(tmp_path):
    with feishu_runtime.Instance(tmp_path, platform="weixin") as instance:
        instance.status("connected", {})
        result = feishu_runtime.inspect(tmp_path, maximum_age=0, platform="weixin")
        assert result["process_state"] == "running"
        assert result["reason"] == "heartbeat_expired"
        assert result["platform"] == "weixin"
        assert result["instance_id"] == instance.identity
        assert not result["available"]
        assert health.transport(result) == "unknown"


def test_missing_channel_observation_keeps_transport_unknown(config, channel):
    value, _ = channel
    current = bind(config, value)
    main = database.connect(config.state_root)
    try:
        with feishu_runtime.Instance(store.home(config, value["id"])) as instance:
            instance.status("connected", {})
            result = health.present(config, main, current)
            assert result["runtime"]["process_state"] == "running"
            assert result["transport"] == "unknown"
            assert not result["capabilities"]["query"]["available"]
    finally:
        main.close()


def test_project_legacy_gateway_is_visible_with_empty_gui_registry(config, channel):
    value, _ = channel
    folder = config.state_root / "configs/hermes-feishu-knowledge"
    folder.mkdir(parents=True)
    raw = {
        "version": 1,
        "enabled": True,
        "app_id": "cli_legacy",
        "owner_id": "ou_synthetic",
        "client_id": value["settings"]["client_id"],
        "vault_id": value["settings"]["vault_id"],
        "home": "configs/hermes-feishu-knowledge",
        "secret_file": "/SYNTHETIC_PRIVATE_FILE",
        "download_limit": 0,
        "store_limit": 0,
    }
    (folder / "receiver.json").write_text(json.dumps(raw))
    main = database.connect(config.state_root)
    try:
        with feishu_runtime.Instance(folder) as instance:
            instance.status("connected", {})
            result = health.overview(config, main)
            old = next(c for c in result["channels"] if c["origin"] == "legacy")
            assert old["transport"] == "connected"
            assert not old["capabilities"]["query"]["available"]
            assert not old["capabilities"]["store"]["available"]
            assert "/SYNTHETIC_PRIVATE_FILE" not in json.dumps(result)
            assert old["end_to_end_verified_at"] is None
    finally:
        main.close()


def test_revoked_permissions_and_old_pair_candidate_are_reported(config, channel):
    value, _ = channel
    current = bind(config, value)
    main = database.connect(config.state_root)
    try:
        main.execute("UPDATE client_vault_grants SET can_query=0")
        current["candidate"] = {"owner_id": "ou_synthetic", "expires_at": time.time() - 1}
        result = health.present(config, main, current)
        assert result["binding_state"] == "candidate_expired"
        assert result["capabilities"]["query"]["reason"] == "permission_revoked"
    finally:
        main.close()


def test_overview_is_admin_only_and_does_not_return_credentials(http, config, channel):
    response = http.get("/admin/channels/overview")
    assert response.status_code == 200
    assert response.json()["schema"] == "channel-overview/v1"
    assert "synthetic-secret" not in response.text
    http.cookies.clear()
    assert http.get("/admin/channels/overview").status_code in {401, 403}


def test_legacy_schema_status_http_reads_do_not_migrate(http, config, channel):
    value, _ = channel
    with store.opened(config) as side:
        side.execute("DROP TABLE channel_operations")
        side.execute("DROP TABLE legacy_channels")
        side.execute("PRAGMA user_version=1")
    path = config.state_root / "message-channels.sqlite3"
    before = path.read_bytes()
    for route in ("", "/overview", f"/{value['id']}/events"):
        assert http.get("/admin/channels" + route).status_code == 200
    assert path.read_bytes() == before


@pytest.mark.parametrize("bad", ["not json", "{}", '{"version": 2}'])
def test_bad_legacy_config_is_visible_as_issue_not_an_empty_success(config, store, bad):
    folder = config.state_root / "configs/hermes-feishu-broken"
    folder.mkdir(parents=True)
    (folder / "receiver.json").write_text(bad)
    main = database.connect(config.state_root)
    try:
        result = health.overview(config, main)
        assert result["issues"] == [{"reason": "legacy_configuration_unreadable"}]
    finally:
        main.close()
