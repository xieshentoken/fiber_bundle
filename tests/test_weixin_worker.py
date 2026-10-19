"""Independent bounded startup, durable claims and local-only executor integration."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
from test_answer_tasks import response
from test_execution import policy as policy
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows
from test_weixin_receiver import Adapter
from test_weixin_store import main_rows
from test_weixin_task_dispatch import finish, queued, two_articles

from fiber_bundle import answer_tasks, auth, database, execution, model_gateway
from fiber_bundle import execution_ledger as ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_receiver as receiver
from fiber_bundle import weixin_task_dispatch as dispatch
from fiber_bundle import weixin_task_status as status
from fiber_bundle import weixin_worker as worker
from fiber_bundle.config import InvalidInput
from fiber_bundle.weixin_worker_config import WorkerConfig, load_worker_config


@pytest.fixture
def settings(binding):
    return WorkerConfig(binding, enabled=True, maximum=2, poll_limit=5, poll_seconds=0.01)


def startup_files(config, settings, policy):
    (config.project_root / "worker.json").write_text(json.dumps(asdict(settings)))
    policy_path = config.project_root / settings.execution_config
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps(asdict(policy)))
    return config.project_root / "worker.json"


def command(config, action="run", *extra):
    return [
        sys.executable,
        "-m",
        "fiber_bundle.weixin_worker_cli",
        action,
        "--project-root",
        str(config.project_root),
        "--config",
        "config.json",
        "--worker-config",
        "worker.json",
        *extra,
    ]


def environment():
    return {**os.environ, "PYTHONPATH": str(Path(worker.__file__).parent.parent)}


def cli(config, action="run", *extra):
    return subprocess.run(
        command(config, action, *extra),
        env=environment(),
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_disabled_needs_no_inbox_policy_secret_or_worker_lock(config):
    from fiber_bundle.weixin_events import Binding

    disabled = WorkerConfig(Binding("bot", "owner", "client", "vault"))
    result = worker.run(config, disabled)
    assert result == {"state": "disabled", "processed": 0, "polls": 0, "results": []}
    assert not config.state_root.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", 1),
        ("version", True),
        ("version", 2),
        ("maximum", True),
        ("maximum", -1),
        ("maximum", 101),
        ("maximum", 1.5),
        ("poll_limit", True),
        ("poll_limit", 0),
        ("poll_limit", 101),
        ("poll_seconds", True),
        ("poll_seconds", 0),
        ("poll_seconds", 301),
        ("poll_seconds", float("nan")),
        ("poll_seconds", float("inf")),
        ("execution_config", "../secret"),
        ("execution_config", "/tmp/private"),
        ("execution_config", None),
        ("binding", None),
    ],
)
def test_invalid_settings_reject_before_execution(config, settings, policy, field, value):
    with patch.object(execution, "execute") as execute, pytest.raises(InvalidInput):
        worker.run(config, replace(settings, **{field: value}), policy)
    execute.assert_not_called()


def test_zero_budget_and_disabled_switch_skip_policy_and_db(config):
    from fiber_bundle.weixin_events import Binding

    settings = WorkerConfig(Binding("bot", "owner", "client", "vault"), enabled=True)
    assert worker.run(config, settings)["state"] == "disabled"
    assert worker.run(config, replace(settings, enabled=False, maximum=2))["state"] == "disabled"
    assert not config.state_root.exists()


def test_enabled_missing_or_invalid_policy_rejected(config, settings, policy):
    with pytest.raises(InvalidInput, match="explicit"):
        worker.run(config, settings)
    with pytest.raises(InvalidInput):
        worker.run(config, settings, replace(policy, daily_calls=0))


def test_file_config_exact_fields_bound_size_and_symlinks(config, settings, policy):
    path = startup_files(config, settings, policy)
    assert load_worker_config(config, path) == settings
    data = json.loads(path.read_text())
    data["unexpected"] = "private data"
    path.write_text(json.dumps(data))
    with pytest.raises(InvalidInput):
        load_worker_config(config, path)
    path.write_text("x" * 65537)
    with pytest.raises(InvalidInput):
        load_worker_config(config, path)
    link = config.project_root / "alias.json"
    link.symlink_to(path)
    with pytest.raises(InvalidInput):
        load_worker_config(config, link)
    with pytest.raises(InvalidInput):
        load_worker_config(config, config.project_root.parent / "outside.json")


@pytest.mark.parametrize(
    "binding_value",
    [
        None,
        "not an object",
        {"account_id": "bot"},
        {
            "account_id": "bot",
            "owner_id": "owner",
            "client_id": "client",
            "vault_id": "vault",
            "token": "synthetic private value",
        },
    ],
)
def test_bad_binding_file_rejected(config, settings, policy, binding_value):
    path = startup_files(config, settings, policy)
    data = asdict(settings)
    data["binding"] = binding_value
    path.write_text(json.dumps(data))
    with pytest.raises(InvalidInput):
        load_worker_config(config, path)


def test_total_budget_spans_polls_and_restart_never_replays(config, binding, policy, settings):
    _, runs = two_articles(config, binding, policy)
    one = replace(settings, maximum=1)
    with patch.object(execution, "execute", side_effect=finish) as execute:
        first = worker.run(config, one, policy)
        second = worker.run(config, one, policy)
        third = worker.run(config, one, policy)
    assert first["state"] == second["state"] == "limit_reached"
    assert first["results"][0]["run_id"] == runs[0]
    assert second["results"][0]["run_id"] == runs[1]
    assert third["state"] == "poll_limit_reached" and third["processed"] == 0
    assert third["polls"] == settings.poll_limit and execute.call_count == 2


def test_total_count_not_reset_each_poll(config, binding, policy, settings):
    _, runs = two_articles(config, binding, policy)
    with patch.object(execution, "execute", side_effect=finish):
        result = worker.run(config, settings, policy)
    assert result["state"] == "limit_reached" and result["processed"] == 2 and result["polls"] == 2
    assert [item["run_id"] for item in result["results"]] == runs


def test_session_lock_held_across_idle_wait_and_stop_event(config, settings, policy):
    entered = Event()
    stop = Event()

    class Wait(Event):
        def wait(self, timeout=None):
            entered.set()
            return super().wait(timeout)

    waiting = Wait()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            worker.run, config, replace(settings, poll_seconds=30), policy, stop=waiting
        )
        assert entered.wait(5)
        with pytest.raises(InvalidInput, match="already active"):
            worker.run(config, settings, policy)
        waiting.set()
        assert pending.result(timeout=5)["state"] == "interrupted"
    with worker.session_lock(config):
        pass
    stop.set()
    assert worker.run(config, settings, policy, stop=stop)["processed"] == 0


def test_awaited_task_arrives_without_restarting_worker(config, binding, settings, policy):
    entered = Event()
    submitted = Event()

    class Wait(Event):
        def wait(self, timeout=None):
            entered.set()
            assert submitted.wait(5)
            return False

    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        patch.object(execution, "execute", side_effect=finish),
    ):
        pending = pool.submit(worker.run, config, replace(settings, maximum=1), policy, stop=Wait())
        assert entered.wait(5)
        _, run_id = queued(config, binding, policy)
        submitted.set()
        result = pending.result(timeout=5)
    assert result["processed"] == 1 and result["results"][0]["run_id"] == run_id


def test_receiver_captures_during_execution_without_waiting(config, binding, policy, settings):
    _, run_id = queued(config, binding, policy)
    entered = Event()
    release = Event()

    def held(config, policy, run_id, prompt):
        with execution.run_lock(config, run_id):
            entered.set()
            assert release.wait(5)
            return finish(config, policy, run_id, prompt)

    with (
        patch.object(execution, "execute", side_effect=held),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        pending = pool.submit(worker.run, config, replace(settings, maximum=1), policy)
        assert entered.wait(5)
        adapter = Adapter()
        receiver.configure(adapter, config, binding)
        adapter._running = True

        async def fetch(*args, **kwargs):
            assert not release.is_set() and not pending.done()
            adapter._running = False
            return poll(message("during-execution"), cursor="concurrent-capture")

        asyncio.run(receiver.run(adapter, fetch))
        assert inbox.cursor(config, binding) == "concurrent-capture" and not adapter.errors
        assert any(item["event_id"] == "during-execution" for item in rows(config, "events"))
        release.set()
        result = pending.result(timeout=5)
    assert result["results"][0]["run_id"] == run_id


def test_failure_stops_remaining_and_preserves_success(config, binding, policy, settings):
    _, runs = two_articles(config, binding, policy)

    def fail(config, policy, run_id, prompt):
        db = database.connect(config.state_root)
        ledger.transition(db, run_id, "failed", "synthetic_failure")
        db.close()
        return {"state": "succeeded"}

    with patch.object(execution, "execute", side_effect=fail) as execute:
        result = worker.run(config, settings, policy)
    assert result["state"] == "stopped" and result["processed"] == 1 and execute.call_count == 1
    assert main_rows(config, "execution_runs")[1]["state"] == "queued"
    assert result["results"][0]["run_id"] == runs[0]


def test_cancel_between_successful_tasks_blocks_next_claim(config, binding, policy, settings):
    bid, runs = two_articles(config, binding, policy)

    def cancel_after(*args):
        result = finish(*args)
        batches.choose(config, binding, bid, "cancel")
        return result

    with patch.object(execution, "execute", side_effect=cancel_after) as execute:
        result = worker.run(config, settings, policy)
    assert result["state"] == "stopped" and execute.call_count == 1
    assert status.synchronize(config, binding, runs[1])["run_state"] == "cancelled"
    assert main_rows(config, "execution_runs")[0]["state"] == "succeeded"


def test_wrong_binding_and_policy_cannot_execute(config, binding, policy, settings):
    queued(config, binding, policy)
    with patch.object(execution, "execute") as execute:
        with pytest.raises(InvalidInput):
            worker.run(
                config, replace(settings, binding=replace(binding, owner_id="forged")), policy
            )
        result = worker.run(config, settings, replace(policy, max_attempts=3))
    assert result["processed"] == 0
    execute.assert_not_called()


def test_status_reconcile_do_not_read_secret_or_execute(config, binding, policy, settings):
    _, run_id = queued(config, binding, policy)
    startup_files(config, replace(settings, enabled=False), policy)
    assert not Path(policy.secret_file).exists()
    result = cli(config, "status", "--run-id", run_id)
    assert result.returncode == 0 and json.loads(result.stdout)["run_state"] == "queued"
    result = cli(config, "reconcile", "--run-id", run_id)
    assert result.returncode == 0 and json.loads(result.stdout)["dispatch_phase"] == "observed"
    assert main_rows(config, "model_calls") == []
    assert main_rows(config, "execution_runs")[0]["state"] == "queued"


def test_cli_disabled_does_not_load_policy_or_initialize_inbox(config, settings, policy):
    startup_files(config, replace(settings, enabled=False), policy)
    Path(config.project_root / settings.execution_config).unlink()
    result = cli(config)
    assert result.returncode == 0 and json.loads(result.stdout)["state"] == "disabled"
    assert not (config.state_root / "weixin-execution-worker.lock").exists()


def test_invalid_enabled_cli_configuration_is_categorical(config, settings, policy):
    startup_files(config, settings, replace(policy, daily_limit_nusd=0))
    result = cli(config)
    assert result.returncode == 1 and json.loads(result.stdout)["state"] == "error"
    assert not result.stderr and policy.secret_file not in result.stdout
    assert not (config.state_root / "weixin-execution-worker.lock").exists()


def test_process_crash_after_claim_restart_stays_blocked(config, binding, settings, policy):
    _, run_id = queued(config, binding, policy)
    startup_files(config, settings, policy)
    script = """
import os
from fiber_bundle import execution, weixin_worker_cli
execution.execute=lambda *args: os._exit(73)
raise SystemExit(weixin_worker_cli.main())
"""
    args = command(config)
    result = subprocess.run(
        [args[0], "-c", script, *args[3:]],
        env=environment(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 73
    assert status.synchronize(config, binding, run_id)["dispatch_phase"] == "claimed"
    restarted = cli(config)
    assert restarted.returncode == 1
    assert json.loads(restarted.stdout)["blocked"]["error_code"] == "task_dispatch_unresolved"
    reconciled = cli(config, "reconcile", "--run-id", run_id)
    assert json.loads(reconciled.stdout)["dispatch_phase"] == "unknown"
    assert cli(config).returncode == 1
    assert main_rows(config, "model_calls") == []
    assert len(main_rows(config, "execution_runs")) == 1


def test_cli_sigterm_releases_session_lock_without_creating_tasks(config, settings, policy):
    startup_files(config, replace(settings, poll_seconds=30, poll_limit=100), policy)
    child = subprocess.Popen(
        command(config),
        env=environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        lock = config.state_root / "weixin-execution-worker.lock"
        while not lock.exists() and time.monotonic() < deadline:
            assert child.poll() is None
            time.sleep(0.01)
        assert lock.exists()
        second = cli(config)
        assert second.returncode == 1 and json.loads(second.stdout)["state"] == "error"
        child.send_signal(signal.SIGTERM)
        output, errors = child.communicate(timeout=5)
        assert child.returncode == 130
        assert json.loads(output)["state"] == "interrupted" and not errors
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
    with worker.session_lock(config):
        pass
    assert main_rows(config, "model_calls") == []
    assert main_rows(config, "execution_runs") == []


PROTOCOL = """
import json,sys
json.loads(sys.stdin.readline())
print(json.dumps({'type':'model','id':'1','body':{}}),flush=True)
reply=json.loads(sys.stdin.readline())
if reply['ok']:
    print(json.dumps({'type':'event','event':{'type':'agent_end','messages':[{'role':'assistant','stopReason':'stop'}]}}),flush=True)
sys.stdin.read()
"""


@pytest.mark.parametrize("mode", ["success", "budget", "daily_budget", "cancel", "revoke"])
def test_native_executor_budget_cancel_and_authorization(config, binding, policy, settings, mode):
    if mode == "budget":
        policy = replace(policy, task_limit_nusd=1)
    if mode == "daily_budget":
        policy = replace(policy, daily_limit_nusd=1)
    bid, run_id = queued(config, binding, policy)

    def synthetic(p, r, payload):
        if mode == "cancel":
            batches.choose(config, binding, bid, "cancel")
            status.synchronize(config, binding, run_id)
        elif mode == "revoke":
            db = database.connect(config.state_root)
            auth.set_grant(
                db, binding.client_id, binding.vault_id, query=False, original=False, add=True
            )
            db.close()
        return response(payload)

    with (
        patch.object(execution, "command", return_value=[sys.executable, "-u", "-c", PROTOCOL]),
        patch.object(execution, "stop"),
        patch.object(model_gateway, "complete", side_effect=synthetic) as provider,
    ):
        result = worker.run(config, replace(settings, maximum=1), policy)
    expected = {
        "success": "succeeded",
        "budget": "waiting_budget",
        "daily_budget": "waiting_budget",
        "cancel": "cancelled",
        "revoke": "failed",
    }[mode]
    assert result["results"][0]["run_state"] == expected
    assert result["state"] == ("limit_reached" if mode == "success" else "stopped")
    calls = main_rows(config, "model_calls")
    assert provider.call_count == (0 if mode in {"budget", "daily_budget"} else 1)
    assert len(calls) == (0 if mode in {"budget", "daily_budget"} else 1)
    if calls:
        assert calls[0]["state"] == "settled"
    if mode == "success":
        db = database.connect(config.state_root)
        assert answer_tasks.deliver(config, db, binding.client_id, run_id)
        db.close()


def test_revocation_between_successful_tasks_stops_next(config, binding, settings, policy):
    _, runs = two_articles(config, binding, policy)

    def denied_after(*args):
        finish(*args)
        db = database.connect(config.state_root)
        auth.set_grant(
            db, binding.client_id, binding.vault_id, query=False, original=False, add=False
        )
        db.close()

    with patch.object(execution, "execute", side_effect=denied_after) as execute:
        with pytest.raises(auth.Denied):
            worker.run(config, settings, policy)
    assert execute.call_count == 1
    assert main_rows(config, "execution_runs")[0]["state"] == "succeeded"
    assert main_rows(config, "execution_runs")[1]["id"] == runs[1]
    assert main_rows(config, "execution_runs")[1]["state"] == "queued"
    assert not main_rows(config, "model_calls")


def test_stopped_event_after_success_prevents_next_claim(config, binding, settings, policy):
    _, runs = two_articles(config, binding, policy)
    stop = Event()

    def stopped_after(*args):
        result = finish(*args)
        stop.set()
        return result

    with patch.object(execution, "execute", side_effect=stopped_after) as execute:
        result = worker.run(config, settings, policy, stop=stop)
    assert result["state"] == "interrupted" and result["processed"] == 1
    assert execute.call_count == 1
    assert result["results"][0]["run_id"] == runs[0]
    assert main_rows(config, "execution_runs")[1]["state"] == "queued"


def test_process_signal_during_native_execution_retains_claim_until_reconcile(
    config, binding, settings, policy
):
    _, run_id = queued(config, binding, policy)
    startup_files(config, settings, policy)
    script = """
import sys
from fiber_bundle import execution, weixin_worker_cli
protocol = "import sys; from pathlib import Path; Path(sys.argv[1]).touch(); sys.stdin.read()"
def synthetic(p, task, r, **kw):
    return [sys.executable, '-u', '-c', protocol, str(task/'started')]
execution.command=synthetic
execution.stop=lambda *args: None
raise SystemExit(weixin_worker_cli.main())
"""
    args = command(config)
    child = subprocess.Popen(
        [args[0], "-c", script, *args[3:]],
        env=environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not (config.state_root / "tasks" / run_id / "started").exists():
            assert child.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        child.send_signal(signal.SIGTERM)
        output, errors = child.communicate(timeout=5)
        assert child.returncode == 130 and json.loads(output)["state"] == "interrupted"
        assert not errors
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
    assert status.synchronize(config, binding, run_id)["dispatch_phase"] == "claimed"
    assert main_rows(config, "execution_runs")[0]["state"] == "failed"
    assert cli(config).returncode == 1
    result = cli(config, "reconcile", "--run-id", run_id)
    assert json.loads(result.stdout)["dispatch_phase"] == "finished"
    assert not main_rows(config, "model_calls")


def test_exception_after_reserved_call_keeps_uncertain_fees_and_does_not_retry(
    config, binding, settings, policy
):
    _, run_id = queued(config, binding, policy)

    def interrupted(config, policy, run_id, prompt):
        with database.locked(config):
            db = database.connect(config.state_root)
            ledger.start(config, db, run_id, policy)
            ledger.reserve(config, db, run_id, policy)
            db.close()
        raise RuntimeError("synthetic private provider text")

    with patch.object(execution, "execute", side_effect=interrupted):
        with pytest.raises(RuntimeError):
            worker.run(config, settings, policy)
    before = main_rows(config, "model_calls")
    assert len(before) == 1 and before[0]["state"] == "reserved"
    with patch.object(execution, "execute") as execute:
        result = worker.run(config, settings, policy)
        assert result["state"] == "blocked" and result["processed"] == 0
        assert dispatch.reconcile(config, binding, run_id)["dispatch_phase"] == "unknown"
        execute.assert_not_called()
    assert main_rows(config, "model_calls") == before
    assert status.synchronize(config, binding, run_id)["dispatch_phase"] == "unknown"


def test_poll_budget_also_caps_successful_execution(config, binding, policy, settings):
    _, runs = two_articles(config, binding, policy)
    with patch.object(execution, "execute", side_effect=finish) as execute:
        result = worker.run(config, replace(settings, poll_limit=1), policy)
    assert result["state"] == "poll_limit_reached"
    assert result["processed"] == result["polls"] == execute.call_count == 1
    assert result["results"][0]["run_id"] == runs[0]
    assert main_rows(config, "execution_runs")[1]["state"] == "queued"


def test_cli_execution_exception_does_not_echo_private_text(config, binding, policy, settings):
    _, run_id = queued(config, binding, policy)
    startup_files(config, settings, policy)
    script = """
from fiber_bundle import execution, weixin_worker_cli
def fail(*args):
    raise RuntimeError('synthetic private provider payload')
execution.execute=fail
raise SystemExit(weixin_worker_cli.main())
"""
    args = command(config)
    result = subprocess.run(
        [args[0], "-c", script, *args[3:]],
        env=environment(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1 and not result.stderr
    assert json.loads(result.stdout) == {"state": "error", "error_code": "weixin_worker_rejected"}
    assert status.synchronize(config, binding, run_id)["dispatch_phase"] == "unknown"
    assert main_rows(config, "model_calls") == []
    assert cli(config).returncode == 1
