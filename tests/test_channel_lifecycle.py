"""Observable lifecycle, credential ownership, durable intents and restore compatibility."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from test_message_channels import bind
from test_message_channels import channel as channel

from fiber_bundle import database, feishu_runtime, restore_rebind
from fiber_bundle.channels import (
    configuration,
    control,
    health,
    legacy,
    legacy_control,
    lifecycle,
    lifecycle_execution,
    operations,
    query,
    store,
    worker,
)
from fiber_bundle.config import InvalidInput


def request(config, identity, action, **changes):
    preview = lifecycle.preview(config, identity, action)
    return lifecycle.Request(
        action=action,
        operation_id=str(uuid4()),
        expected_revision=preview["revision"],
        configuration_sha256=preview["configuration_sha256"],
        expected_instance_id=preview["instance_id"],
        **changes,
    )


def begin(config, identity, action):
    main = database.connect(config.state_root)
    try:
        return lifecycle.begin(config, main, identity, request(config, identity, action))
    finally:
        main.close()


def tokens(config, identity):
    home = store.home(config, identity)
    private = home / "weixin/accounts/synthetic.json"
    private.parent.mkdir(parents=True)
    private.write_text('{"token":"SYNTHETIC_LOCAL_TOKEN"}')
    (home / "weixin-login.json").write_text('{"owner_id":"ou_synthetic"}')
    return home, private


@pytest.mark.parametrize("platform", ["feishu", "weixin"])
def test_unbind_clears_owned_login_but_preserves_budget_audit_and_feishu_secret(
    config, channel, platform
):
    value, root = channel
    current = bind(config, value)
    with store.opened(config) as side:
        settings = {**current["value"], "platform": platform}
        side.execute("UPDATE channels SET value=? WHERE id=?", (json.dumps(settings), value["id"]))
        side.execute(
            "INSERT INTO events VALUES(?,?,1,'sent',?,?)",
            (value["id"], "spent", '{"model_calls_reserved":1}', time.time()),
        )
    home, token = tokens(config, value["id"])
    original = (root / "a.md").read_bytes()
    result = begin(config, value["id"], "unbind")
    assert result["state"] == "completed"
    assert not token.exists() and not (home / "weixin-login.json").exists()
    assert configuration.secret_path(config, value["id"]).is_file()
    with store.opened(config) as side:
        record = store.get(side, value["id"])
        assert record["state"] == "unpaired" and record["owner"] is None
        assert record["revision"] == 1
        assert side.execute("SELECT state,value FROM events").fetchone()[0] == "sent"
    assert (root / "a.md").read_bytes() == original


def test_remove_retains_tombstone_notes_shared_grants_and_ledger(config, channel):
    value, root = channel
    bind(config, value)
    home, token = tokens(config, value["id"])
    original = (root / "a.md").read_bytes()
    main = database.connect(config.state_root)
    before = [tuple(row) for row in main.execute("SELECT * FROM client_vault_grants")]
    data = request(config, value["id"], "remove")
    result = lifecycle.begin(config, main, value["id"], data)
    assert result["state"] == "completed"
    assert lifecycle.begin(config, main, value["id"], data) == result
    assert not token.exists() and not configuration.secret_path(config, value["id"]).exists()
    assert home.is_dir() and (home / "fiber-instance.lock").exists()
    assert before == [tuple(row) for row in main.execute("SELECT * FROM client_vault_grants")]
    overview = health.overview(config, main)
    assert overview["channels"] == [] and overview["removed_channels"][0]["id"] == value["id"]
    with pytest.raises(InvalidInput):
        control.launch(config, main, value["id"], "run")
    with pytest.raises(InvalidInput, match="removed"):
        query.test(config, main, value["id"], "压力", False)
    main.close()
    assert (root / "a.md").read_bytes() == original


def test_running_process_must_exit_before_credentials_are_cleaned(config, channel, monkeypatch):
    value, _ = channel
    bind(config, value)
    home, token = tokens(config, value["id"])
    launched = []
    monkeypatch.setattr(lifecycle, "launch", lambda *args: launched.append(args))
    with feishu_runtime.Instance(home) as instance:
        instance.status("connected", {})
        result = begin(config, value["id"], "unbind")
        assert result["state"] == "pending" and len(launched) == 1
        assert token.is_file()
        main = database.connect(config.state_root)
        with pytest.raises(InvalidInput, match="pending"):
            control.pause(config, main, value["id"])
        main.close()
        assert not lifecycle_execution.advance(config, result["id"])
        assert token.is_file()
    assert lifecycle_execution.advance(config, result["id"])
    assert not token.exists()


def test_stale_preview_and_reused_operation_id_have_no_cleanup_effect(config, channel):
    value, _ = channel
    data = request(config, value["id"], "remove")
    with store.opened(config) as side:
        side.execute("UPDATE channels SET revision=2")
    main = database.connect(config.state_root)
    try:
        with pytest.raises(InvalidInput, match="conflict"):
            lifecycle.begin(config, main, value["id"], data)
        assert configuration.secret_path(config, value["id"]).is_file()
        data = request(config, value["id"], "pause")
        lifecycle.begin(config, main, value["id"], data)
        changed = data.model_copy(update={"action": "remove"})
        with pytest.raises(InvalidInput, match="conflict"):
            lifecycle.begin(config, main, value["id"], changed)
    finally:
        main.close()


def test_unknown_send_blocks_final_remove_until_explicit_phone_reconciliation(
    http, config, channel
):
    value, _ = channel
    bind(config, value)
    with store.opened(config) as side:
        side.execute(
            "INSERT INTO events VALUES(?,?,1,'unknown','{}',?)",
            (value["id"], "uncertain", time.time()),
        )
    result = begin(config, value["id"], "remove")
    assert result["state"] == "needs_review"
    assert result["reason"] == "channel_delivery_requires_reconciliation"
    assert configuration.secret_path(config, value["id"]).is_file()
    response = http.post(
        f"/admin/channels/{value['id']}/reconcile",
        json={"event_id": "uncertain", "acknowledgement": "not_sent"},
    )
    assert response.status_code == 200
    assert lifecycle_execution.advance(config, result["id"])
    with store.opened(config) as side:
        assert side.execute("SELECT state FROM events").fetchone()[0] == "not_sent"
        assert store.get(side, value["id"])["state"] == "removed"


def test_symlink_or_shared_file_blocks_cleanup_and_preserves_external_data(
    config, channel, tmp_path
):
    value, _ = channel
    home = store.home(config, value["id"])
    external = tmp_path / "external-token.txt"
    external.write_text("SYNTHETIC_EXTERNAL_PRESERVED")
    (home / "weixin").symlink_to(external)
    result = begin(config, value["id"], "remove")
    assert result["state"] == "needs_review"
    assert external.read_text() == "SYNTHETIC_EXTERNAL_PRESERVED"
    assert configuration.secret_path(config, value["id"]).exists()


def legacy_entry(config, channel, private=True):
    value, _ = channel
    home = config.state_root / "configs/hermes-feishu-existing"
    home.mkdir(parents=True)
    secret = (
        home / "credential.json" if private else config.project_root / "external-credential.json"
    )
    secret.write_text('{"app_secret":"SYNTHETIC_LEGACY_SECRET"}')
    raw = {
        "version": 1,
        "enabled": True,
        "app_id": "cli_legacy",
        "owner_id": "ou_synthetic",
        "home": home.relative_to(config.state_root).as_posix(),
        "client_id": value["settings"]["client_id"],
        "vault_id": value["settings"]["vault_id"],
        "secret_file": str(secret),
    }
    path = home / "receiver.json"
    path.write_text(json.dumps(raw))
    return legacy.entry(config, path), path, secret


@pytest.mark.parametrize("private", [True, False])
def test_legacy_removal_clears_only_owned_configuration_and_keeps_tombstone(
    config, channel, private
):
    entry, path, secret = legacy_entry(config, channel, private)
    result = begin(config, entry["id"], "remove")
    assert result["state"] == "completed"
    assert not path.exists()
    assert secret.exists() is not private
    main = database.connect(config.state_root)
    try:
        overview = health.overview(config, main)
        assert not any(c["id"] == entry["id"] for c in overview["channels"])
        assert overview["removed_channels"][0]["id"] == entry["id"]
        with pytest.raises(InvalidInput, match="withdrawn"):
            legacy_control.guard(config, entry["value"]["home"])
    finally:
        main.close()


def test_legacy_unbind_disables_receiver_and_retains_app_credentials(config, channel):
    entry, path, secret = legacy_entry(config, channel)
    result = begin(config, entry["id"], "unbind")
    assert result["state"] == "completed"
    assert json.loads(path.read_text())["owner_id"] == "ou_unpaired"
    assert not json.loads(path.read_text())["enabled"]
    assert secret.exists()


@pytest.mark.parametrize("phase", ["capture", "dispatch"])
def test_withdrawn_legacy_gateway_cannot_admit_or_dispatch_expert_commands(config, channel, phase):
    from test_feishu_ingress import payload

    from fiber_bundle import auth, expert_feishu, feishu_receiver, weixin_inbox
    from fiber_bundle.weixin_events import Binding

    value, _ = channel
    entry, _, _ = legacy_entry(config, channel)
    main = database.connect(config.state_root)
    cid, vid = value["settings"]["client_id"], value["settings"]["vault_id"]
    main.execute("UPDATE vaults SET read_only=0 WHERE id=?", (vid,))
    auth.set_grant(main, cid, vid, query=True, original=False, add=True)
    main.close()
    binding = Binding("feishu:cli_synthetic", "ou_owner", cid, vid)
    feishu_receiver.initialize(config, binding)
    authorization = str(uuid4())
    raw = payload(body={"text": f"/fake-expert start {uuid4()}"})
    options = {"expert_authorization": authorization, "expert_limit": 1}
    if phase == "dispatch":
        feishu_receiver.capture(config, binding, raw, **options)
    begin(config, entry["id"], "pause")

    def guard():
        legacy_control.guard(config, entry["value"]["home"])

    with pytest.raises(InvalidInput, match="withdrawn"):
        if phase == "capture":
            feishu_receiver.capture(config, binding, raw, **options, guard=guard)
        else:
            expert_feishu.dispatch_one(config, binding, authorization, guard=guard)
    with weixin_inbox.connection(config, binding) as journal:
        if phase == "capture":
            # Explicit receiver initialization installs the v2 ledger; a withdrawn
            # gateway must still leave it empty and admit no command.
            assert not journal.execute("SELECT 1 FROM fiber_expert_commands").fetchone()
        else:
            assert (
                journal.execute("SELECT state FROM fiber_expert_commands").fetchone()[0] == "queued"
            )


def test_legacy_signal_rejects_a_different_process_even_with_live_lease(config, channel):
    entry, path, secret = legacy_entry(config, channel)
    with feishu_runtime.Instance(path.parent) as instance:
        instance.status("connected", {})
        with pytest.raises(InvalidInput, match="unverified"):
            legacy_control.signal_stop(config, entry, instance.identity)
    assert secret.exists()


@pytest.mark.parametrize("python_flags", [[], ["-s"]])
def test_legacy_signal_requires_matching_module_settings_and_fresh_instance(
    config, channel, monkeypatch, python_flags
):
    import signal

    entry, path, _ = legacy_entry(config, channel)
    signals = []
    with feishu_runtime.Instance(path.parent) as instance:
        instance.status("connected", {})
        command = [
            sys.executable,
            *python_flags,
            "-m",
            "fiber_bundle.feishu_gateway_cli",
            "--settings",
            str(path),
            "--project-root",
            str(config.project_root),
        ]
        monkeypatch.setattr(legacy_control.process_args, "arguments", lambda pid: command)
        monkeypatch.setattr(legacy_control.os, "kill", lambda pid, sig: signals.append((pid, sig)))
        legacy_control.signal_stop(config, entry, instance.identity)
        assert signals == [(os.getpid(), signal.SIGTERM)]
        signals.clear()
        with pytest.raises(InvalidInput, match="unverified"):
            legacy_control.signal_stop(config, entry, str(uuid4()))
        assert signals == []


def test_delayed_removed_worker_does_not_bootstrap_or_recreate_credentials(
    config, channel, monkeypatch
):
    value, _ = channel
    begin(config, value["id"], "remove")
    monkeypatch.setattr(
        worker.feishu_gateway_cli,
        "bootstrap",
        lambda *args: pytest.fail("removed worker bootstrapped"),
    )
    with pytest.raises(InvalidInput, match="withdrawn"):
        asyncio.run(worker.run(config, {"channel_id": value["id"], "revision": 1, "action": "run"}))


def test_schema_one_is_read_without_migration_and_upgrades_preserving_records(config, channel):
    value, _ = channel
    with store.opened(config) as side:
        side.execute("DROP TABLE channel_operations")
        side.execute("DROP TABLE legacy_channels")
        side.execute("PRAGMA user_version=1")
    path = config.state_root / "message-channels.sqlite3"
    before = path.read_bytes()
    main = database.connect(config.state_root)
    health.overview(config, main)
    main.close()
    assert before == path.read_bytes()
    with store.opened(config) as side:
        assert side.execute("PRAGMA user_version").fetchone()[0] == 3
        assert store.get(side, value["id"])["revision"] == 1


def test_restore_does_not_resurrect_removed_channels_or_resume_cleanup(config, channel):
    value, _ = channel
    begin(config, value["id"], "remove")
    with store.opened(config) as side:
        side.execute(
            "INSERT INTO channel_operations VALUES(?,?,?,'pending','{}',?,?,NULL)",
            (str(uuid4()), str(uuid4()), "unbind", time.time(), time.time()),
        )
    restore_rebind._quarantine_sidecars(config)
    with store.opened(config) as side:
        assert store.get(side, value["id"])["state"] == "removed"
        assert (
            side.execute(
                "SELECT state FROM channel_operations ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0]
            == "interrupted"
        )


def test_restored_interrupted_intent_cannot_delete_new_credentials(config, channel, monkeypatch):
    value, _ = channel
    bind(config, value)
    home, token = tokens(config, value["id"])
    monkeypatch.setattr(lifecycle, "launch", lambda *args: None)
    with feishu_runtime.Instance(home) as instance:
        instance.status("connected", {})
        result = begin(config, value["id"], "remove")
        assert result["state"] == "pending"
    restore_rebind._quarantine_sidecars(config)
    assert lifecycle_execution.advance(config, result["id"])
    assert token.exists() and configuration.secret_path(config, value["id"]).exists()
    with pytest.raises(InvalidInput, match="restored_confirmation"):
        lifecycle.resume(config, value["id"], result["id"])


def test_lifecycle_http_requires_admin_csrf_and_valid_intent(http, config, channel):
    value, _ = channel
    path = f"/admin/channels/{value['id']}/lifecycle"
    csrf = http.headers.pop("X-CSRF-Token")
    assert http.post(path + "/preview", json={"action": "remove"}).status_code == 403
    http.headers["X-CSRF-Token"] = csrf
    assert http.post(path + "/preview", json={"action": "erase-vault"}).status_code == 422
    data = request(config, value["id"], "remove").model_dump()
    assert http.post(path, json={**data, "operation_id": "../../outside"}).status_code == 422
    http.cookies.clear()
    assert http.post(path, json=data).status_code in {401, 403}
    assert configuration.secret_path(config, value["id"]).exists()


def test_hardlinked_token_blocks_cleanup_before_any_file_is_deleted(config, channel, tmp_path):
    value, _ = channel
    _, token = tokens(config, value["id"])
    shared = tmp_path / "shared-token.json"
    os.link(token, shared)
    result = begin(config, value["id"], "remove")
    assert result["state"] == "needs_review"
    assert token.exists() and shared.exists()
    assert configuration.secret_path(config, value["id"]).exists()


def test_v2_backup_restores_tombstones_but_excludes_legacy_and_gui_credentials(
    config, channel, tmp_path
):
    from fiber_bundle import backup_bundle, backup_preflight, backup_restore
    from fiber_bundle.config import load_config

    value, _ = channel
    old, old_path, old_key = legacy_entry(config, channel)
    begin(config, value["id"], "remove")
    begin(config, old["id"], "pause")
    result = backup_preflight.inspect(config)
    assert {"path": "message-channels.sqlite3", "schema": 3} in result["databases"]
    assert not any("hermes-feishu-" in entry["path"] for entry in result["entries"])
    backup_bundle.create(config, tmp_path / "bundle", "synthetic-password")
    backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", "synthetic-password")
    target = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    backup_restore.activate(target, source_stopped=True)
    with store.opened(target) as side:
        assert store.get(side, value["id"])["state"] == "removed"
    main = database.connect(target.state_root)
    try:
        restored = health.overview(target, main)
    finally:
        main.close()
    old_status = next(c for c in restored["channels"] if c["id"] == old["id"])
    assert old_status["binding_state"] == "configuration_required"
    assert not old_status["start_supported"]
    assert not (target.state_root / old_path.relative_to(config.state_root)).exists()
    assert old_key.exists()


def test_real_detached_cleanup_worker_finishes_after_owned_process_exits(config, channel):
    value, _ = channel
    bind(config, value)
    home, token = tokens(config, value["id"])
    script = (
        "import sys,time,sqlite3;from pathlib import Path;"
        "from fiber_bundle.feishu_runtime import Instance;"
        "p=Instance(Path(sys.argv[1]));p.__enter__();p.status('connected',{});print('ready',flush=True);"
        "db=sqlite3.connect(sys.argv[2]);\n"
        "while db.execute('SELECT state FROM channels WHERE id=?',"
        "(sys.argv[3],)).fetchone()[0]=='running':\n"
        " p.status('connected',{});time.sleep(.05)\n"
        "db.close();p.__exit__(None,None,None)"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(home),
            str(config.state_root / "message-channels.sqlite3"),
            value["id"],
        ],
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        result = begin(config, value["id"], "remove")
        process.wait(timeout=5)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with store.readonly(config) as side:
                completed = operations.get(side, result["id"])["state"] == "completed"
            if completed:
                break
            time.sleep(0.05)
        assert completed and not token.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
