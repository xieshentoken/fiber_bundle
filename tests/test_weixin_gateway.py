"""Startup binding, isolated home and per-runner factories without optional Hermes imports."""

import json
import sys
from dataclasses import asdict, replace
from enum import Enum
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_execution import policy as policy
from test_weixin_inbox import binding as binding
from test_weixin_receiver import Adapter
from test_weixin_worker import startup_files

from fiber_bundle import auth, database, execution
from fiber_bundle import weixin_gateway as gateway
from fiber_bundle.article_worker import CapturePolicy
from fiber_bundle.config import InvalidInput
from fiber_bundle.weixin_receiver_config import ReceiverConfig, load_receiver_config
from fiber_bundle.weixin_worker_config import WorkerConfig


class Platform(Enum):
    WEIXIN = "weixin"
    FEISHU = "feishu"


@pytest.fixture
def setup(config, binding, monkeypatch):
    home = config.state_root / "candidate-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setitem(
        sys.modules, "hermes_constants", SimpleNamespace(get_hermes_home=lambda: home)
    )
    platform_config = SimpleNamespace(
        enabled=True,
        extra={
            "account_id": binding.account_id,
            "allow_from": [binding.owner_id],
            "dm_policy": "allowlist",
            "group_policy": "disabled",
        },
    )
    runner = SimpleNamespace(
        config=SimpleNamespace(
            platforms={Platform.WEIXIN: platform_config},
            multiplex_profiles=False,
            sessions_dir=home / "sessions",
        ),
        adapters={},
        _profile_adapters={},
        _create_adapter=lambda *a: pytest.fail("legacy factory used"),
    )

    class Candidate(Adapter):
        def __init__(self, platform_config):
            super().__init__()
            self._hermes_home = str(home)

        async def _fiber_send_once(self, *args):
            pytest.fail("unexpected send")

        async def _fiber_send_result_once(self, *args):
            pytest.fail("unexpected send")

    return home, runner, Candidate, platform_config


def test_disabled_needs_no_home_runner_inbox_or_hermes(config):
    from fiber_bundle.weixin_events import Binding

    result = gateway.install(
        config,
        ReceiverConfig(Binding("bot", "owner", "client", "vault")),
        None,
        None,
        home=config.state_root / "absent",
    )
    assert result == {"state": "disabled"} and not config.state_root.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", 1),
        ("version", True),
        ("version", 2),
        ("quiet_seconds", False),
        ("quiet_seconds", float("nan")),
        ("quiet_seconds", 301),
        ("intent_routing", 1),
        ("media_limit", True),
        ("store_limit", -1),
        ("submission_limit", 101),
        ("outbound_limit", 1.5),
        ("result_receipts", True),
        ("execution_config", "../private"),
        ("capture_policy", {}),
        ("capture_policy", CapturePolicy(None)),
    ],
)
def test_settings_reject_before_import_or_effects(config, binding, field, value):
    with pytest.raises(InvalidInput):
        replace(ReceiverConfig(binding), **{field: value}).validate(config)


def test_exact_startup_file_size_symlink_and_fields(config, binding):
    path = config.project_root / "receiver.json"
    settings = ReceiverConfig(binding)
    path.write_text(json.dumps(asdict(settings)))
    assert load_receiver_config(config, path) == settings
    data = asdict(settings)
    data["token"] = "synthetic private token"
    path.write_text(json.dumps(data))
    with pytest.raises(InvalidInput):
        load_receiver_config(config, path)
    path.write_text("x" * 65537)
    with pytest.raises(InvalidInput):
        load_receiver_config(config, path)
    link = config.project_root / "receiver-link.json"
    link.symlink_to(path)
    with pytest.raises(InvalidInput):
        load_receiver_config(config, link)


def test_install_is_capture_only_and_never_uses_legacy_factory(config, binding, setup):
    home, runner, candidate, pc = setup
    with patch.object(execution, "execute") as execute:
        assert gateway.install(
            config, ReceiverConfig(binding, enabled=True), runner, candidate, home=home
        ) == {"state": "configured", "capture_only": True}
        adapter = runner._create_adapter(Platform.WEIXIN, pc)
        assert adapter._fiber_inbox == (config, binding)
        assert adapter.gateway_runner is runner
        assert adapter._fiber_submission_limit == adapter._fiber_outbound_limit == 0
        with pytest.raises(InvalidInput):
            runner._create_adapter(Platform.WEIXIN, pc)
        execute.assert_not_called()
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize(
    "change",
    [
        "feishu",
        "multiplex",
        "started",
        "running",
        "profile",
        "sessions",
        "legacy",
        "account",
        "owner",
        "group",
        "dm",
    ],
)
def test_invalid_runner_scope_never_constructs_adapter(config, binding, setup, change):
    home, runner, candidate, pc = setup
    if change == "feishu":
        runner.config.platforms[Platform.FEISHU] = pc
    elif change == "multiplex":
        runner.config.multiplex_profiles = True
    elif change == "started":
        runner.adapters[Platform.WEIXIN] = object()
    elif change == "running":
        runner._running = True
    elif change == "profile":
        runner._profile_adapters["personal"] = object()
    elif change == "sessions":
        runner.config.sessions_dir = config.project_root / "sessions"
    elif change == "legacy":
        candidate._fiber_capture_version = None
    elif change == "account":
        pc.extra["account_id"] = "other"
    elif change == "owner":
        pc.extra["allow_from"] = [binding.owner_id, "other"]
    elif change == "group":
        pc.extra["group_policy"] = "allowlist"
    elif change == "dm":
        pc.extra["dm_policy"] = "open"
    with pytest.raises(InvalidInput):
        gateway.install(config, ReceiverConfig(binding, enabled=True), runner, candidate, home=home)


@pytest.mark.parametrize("change", ["env", "context", "outside", "symlink"])
def test_home_isolation(config, binding, setup, monkeypatch, change):
    home, runner, candidate, pc = setup
    if change == "env":
        monkeypatch.delenv("HERMES_HOME")
    elif change == "context":
        monkeypatch.setitem(
            sys.modules,
            "hermes_constants",
            SimpleNamespace(get_hermes_home=lambda: config.project_root),
        )
    elif change == "outside":
        home = config.project_root
    elif change == "symlink":
        target = home
        home = config.state_root / "alias"
        home.symlink_to(target, target_is_directory=True)
    with pytest.raises(InvalidInput):
        gateway.install(config, ReceiverConfig(binding, enabled=True), runner, candidate, home=home)


@pytest.mark.parametrize("change", ["platforms", "config", "owner", "env", "disabled"])
def test_scope_rechecked_at_factory_and_no_feishu_or_builtin_fallback(
    config, binding, setup, monkeypatch, change
):
    home, runner, candidate, pc = setup
    gateway.install(config, ReceiverConfig(binding, enabled=True), runner, candidate, home=home)
    if change == "platforms":
        runner.config.platforms[Platform.FEISHU] = pc
    elif change == "config":
        pc = SimpleNamespace(enabled=True, extra=pc.extra)
    elif change == "owner":
        pc.extra["allow_from"] = ["other"]
    elif change == "env":
        monkeypatch.delenv("HERMES_HOME")
    elif change == "disabled":
        pc.enabled = False
    with pytest.raises(InvalidInput):
        runner._create_adapter(Platform.WEIXIN, pc)
    with pytest.raises(InvalidInput):
        runner._create_adapter(Platform.FEISHU, pc)


def test_submission_policy_load_does_not_read_secret_or_execute(config, binding, setup, policy):
    home, runner, candidate, pc = setup
    startup_files(config, WorkerConfig(binding), policy)
    assert not __import__("pathlib").Path(policy.secret_file).exists()
    settings = ReceiverConfig(
        binding,
        enabled=True,
        intent_routing=True,
        media_limit=1,
        store_limit=1,
        submission_limit=1,
        outbound_limit=1,
        result_receipts=True,
        capture_policy=CapturePolicy("sha256:" + "b" * 64),
    )
    result = gateway.install(config, settings, runner, candidate, home=home)
    adapter = runner._create_adapter(Platform.WEIXIN, pc)
    assert not result["capture_only"] and adapter._fiber_execution_policy == policy
    assert adapter._fiber_capture_policy == settings.capture_policy
    with pytest.raises(InvalidInput):
        gateway.install(config, settings, runner, candidate, home=home)


def test_current_revocation_blocks_factory_before_any_platform_connect(config, binding, setup):
    home, runner, candidate, pc = setup
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, add=False, query=False, original=False)
    db.close()
    with pytest.raises(auth.Denied):
        gateway.install(config, ReceiverConfig(binding, enabled=True), runner, candidate, home=home)
