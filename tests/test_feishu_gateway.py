"""Startup isolation and single-use factories without credentials or live SDK calls."""

import faulthandler
import json
import sys
from dataclasses import replace
from enum import Enum
from types import SimpleNamespace

import pytest
from test_feishu_ingress import feishu as feishu
from test_weixin_inbox import binding as binding

from fiber_bundle import feishu_gateway as gateway
from fiber_bundle import feishu_gateway_cli as cli
from fiber_bundle import feishu_pairing
from fiber_bundle.config import InvalidInput


def test_silent_output_supports_gateway_fault_handler_fallback(capsys):
    output = cli.SilentOutput()
    assert output.encoding == "utf-8"
    assert output.write("synthetic-secret-only-value") == 27
    output.flush()
    assert output.isatty() is False
    with pytest.raises(OSError):
        faulthandler.enable(file=output)
    assert capsys.readouterr() == ("", "")


class Platform(Enum):
    FEISHU = "feishu"
    WEIXIN = "weixin"


@pytest.fixture
def setup(config, feishu, monkeypatch):
    settings = gateway.Settings(
        "cli_synthetic",
        feishu.owner_id,
        feishu.client_id,
        feishu.vault_id,
        "configs/hermes-feishu-knowledge",
        "/synthetic/secret.txt",
        enabled=True,
    )
    home = settings.validate(config)
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setitem(
        sys.modules, "hermes_constants", SimpleNamespace(get_hermes_home=lambda: home)
    )
    pc = SimpleNamespace(
        enabled=True,
        extra={
            "app_id": settings.app_id,
            "connection_mode": "websocket",
            "allow_from": [settings.owner_id],
            "dm_policy": "allowlist",
            "group_policy": "disabled",
        },
    )
    runner = SimpleNamespace(
        config=SimpleNamespace(
            platforms={Platform.FEISHU: pc},
            sessions_dir=home / "sessions",
            multiplex_profiles=False,
        ),
        adapters={},
        _profile_adapters={},
    )

    class Adapter:
        _fiber_capture_version = 1

        def __init__(self, pc):
            self.pc = pc

    return settings, runner, Adapter, pc


def test_exact_platform_and_factory_are_single_use(config, setup):
    settings, runner, adapter, pc = setup
    assert gateway.install(config, settings, runner, adapter)["outbound_limit"] == 0
    assert runner._create_adapter(Platform.FEISHU, pc).pc is pc
    with pytest.raises(InvalidInput):
        runner._create_adapter(Platform.FEISHU, pc)
    with pytest.raises(InvalidInput):
        gateway.install(config, settings, runner, adapter)


@pytest.mark.parametrize("change", ["platform", "extra", "multiplex", "home", "running"])
def test_scope_mutation_before_factory_is_rejected(config, setup, monkeypatch, change):
    settings, runner, adapter, pc = setup
    gateway.install(config, settings, runner, adapter)
    if change == "platform":
        runner.config.platforms[Platform.WEIXIN] = pc
    elif change == "extra":
        pc.extra["allow_from"] = ["ou_other"]
    elif change == "multiplex":
        runner.config.multiplex_profiles = True
    elif change == "home":
        monkeypatch.setenv("HERMES_HOME", "/original/home")
    else:
        pc.enabled = False
    with pytest.raises(InvalidInput):
        runner._create_adapter(Platform.FEISHU, pc)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("enabled", 1),
        ("download_limit", -1),
        ("store_limit", True),
        ("home", "configs/hermes-weixin-test"),
        ("quiet_seconds", 0),
        ("secret_file", "relative"),
        ("owner_id", "tenant_user"),
    ],
)
def test_startup_validation(config, setup, field, value):
    settings, _, _, _ = setup
    with pytest.raises(InvalidInput):
        replace(settings, **{field: value}).validate(config)


def test_disabled_does_not_import_hermes_or_open_credentials(config, setup, monkeypatch):
    settings, _, _, _ = setup
    monkeypatch.delitem(sys.modules, "hermes_constants")
    assert gateway.install(config, replace(settings, enabled=False), None, None) == {
        "state": "disabled"
    }


def test_credential_file_bound_to_app_and_not_echoed(config, setup, capsys):
    settings, _, _, _ = setup
    path = config.project_root / "secret.json"
    path.write_text(
        json.dumps({"app_id": settings.app_id, "app_secret": "synthetic-secret-only-value"})
    )
    assert (
        cli.load_secret(replace(settings, secret_file=str(path))) == "synthetic-secret-only-value"
    )
    path.write_text(
        json.dumps({"app_id": "cli_other", "app_secret": "synthetic-secret-only-value"})
    )
    with pytest.raises(InvalidInput):
        cli.load_secret(replace(settings, secret_file=str(path)))
    assert "synthetic-secret-only-value" not in capsys.readouterr().out


def test_text_credentials_and_pairing_candidate_are_explicit(config, setup):
    from test_feishu_ingress import payload

    settings, _, _, _ = setup
    path = config.project_root / "secret.txt"
    path.write_text("id: cli_synthetic\nsecret: synthetic-secret-only-value\n")
    assert (
        cli.load_secret(replace(settings, secret_file=str(path))) == "synthetic-secret-only-value"
    )
    code = "FB-FS-synthetic-1234567890"
    event = payload(body={"text": code})
    assert feishu_pairing.candidate(settings, event, code)["owner_id"] == "ou_owner"
    assert feishu_pairing.candidate(settings, event, "wrong-code") is None
    event["event"]["message"]["chat_type"] = "group"
    assert feishu_pairing.candidate(settings, event, code) is None
