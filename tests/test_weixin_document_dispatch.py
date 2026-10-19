"""Bootstrap and finite hook tests with a fake gateway; defaults have no side effects."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_document_creation_config import creation_policy as creation_policy
from test_document_creation_execution import prepared_task as prepared_task
from test_document_creation_tasks import input_task as input_task
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import weixin_document_dispatch as dispatch
from fiber_bundle.config import InvalidInput
from fiber_bundle.document_creation_config import parse


def test_default_hook_and_cli_do_not_load_storage_keys_or_provider(monkeypatch, capsys):
    from fiber_bundle import document_creation_worker_cli as cli

    monkeypatch.setattr(
        dispatch.weixin_receiver, "preflight", lambda *args: pytest.fail("preflight")
    )
    assert asyncio.run(dispatch.dispatch(SimpleNamespace(), "missing")) == {
        "state": "disabled",
        "processed": 0,
    }
    monkeypatch.setattr(cli, "load_config", lambda *args: pytest.fail("storage"))
    monkeypatch.setattr("sys.argv", ["fiber-document-worker"])
    assert cli.main() == 0
    assert '"state": "disabled"' in capsys.readouterr().out


def test_bootstrap_gates_and_configuration_digest(
    config, owner, prepared_task, creation_policy, monkeypatch
):
    policy = parse(creation_policy)
    obj = SimpleNamespace(_running=False, _poll_task=None)
    identity = SimpleNamespace(client_id=owner[0])
    monkeypatch.setattr(dispatch.weixin_receiver, "preflight", lambda *args: (config, identity))
    with pytest.raises(InvalidInput, match="upload_policy"):
        dispatch.configure(obj, policy, allow_delivery=True)
    dispatch.configure(obj, policy)
    assert asyncio.run(dispatch.dispatch(obj, prepared_task["task_id"]))["state"] == "disabled"
    dispatch.configure(
        obj, policy, allow_inference=True, allow_delivery=True, upload_limit_bytes=1024
    )
    with patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("provider")):
        with pytest.raises(InvalidInput, match="guarded_sender"):
            asyncio.run(dispatch.dispatch(obj, prepared_task["task_id"]))
    dispatch.configure(obj, policy, allow_inference=True)
    obj._fiber_document_configuration_sha256 = "0" * 64
    with pytest.raises(InvalidInput, match="configuration_changed"):
        asyncio.run(dispatch.dispatch(obj, prepared_task["task_id"]))
    obj._running = True
    with pytest.raises(InvalidInput, match="stopped"):
        dispatch.configure(obj, policy)


def test_explicit_hook_forwards_finite_worker_without_enabling_delivery(
    config, owner, prepared_task, creation_policy, monkeypatch
):
    policy = parse(creation_policy)
    obj = SimpleNamespace(_running=False, _poll_task=None)
    identity = SimpleNamespace(client_id=owner[0])
    monkeypatch.setattr(dispatch.weixin_receiver, "preflight", lambda *args: (config, identity))
    dispatch.configure(obj, policy, allow_inference=True, max_tasks=2)
    seen = []

    def worker(*args, **kwargs):
        seen.append((args, kwargs))
        return [{"state": "succeeded"}]

    monkeypatch.setattr(dispatch.worker, "drain", worker)
    result = asyncio.run(dispatch.dispatch(obj, prepared_task["task_id"]))
    assert result["processed"] == 1 and result["file_deliveries"] == []
    assert seen[0][1] == {"max_tasks": 2, "allow_provider": True}
