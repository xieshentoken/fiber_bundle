import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from unittest.mock import patch

import pytest
from test_local_query_gateway import request, server
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_query_routing_probe import policy as probe_policy
from test_session_search import owner as owner

from fiber_bundle import auth, database, executor_container
from fiber_bundle import local_query_execution as execute
from fiber_bundle import local_query_ledger as ledger
from fiber_bundle import local_query_tasks as tasks
from fiber_bundle import local_query_worker as worker
from fiber_bundle import local_query_worker_cli as cli
from fiber_bundle.config import InvalidInput
from fiber_bundle.local_query_gateway import complete
from fiber_bundle.local_query_process import LocalCallStopped
from fiber_bundle.model_gateway import ModelFailure

PI = """
import json,sys
init=json.loads(sys.stdin.readline())
mode=sys.argv[1]
assert init['taskMode']=='answer'
assert 'secret_file' not in init and 'question' not in init
def emit(v):print(json.dumps(v),flush=True)
end={'type':'event','event':{'type':'agent_end','messages':[{'role':'assistant','stopReason':'stop'}]}}
frame={'type':'model','id':'1','body':{'model':'FORGED',
 'messages':[{'role':'user','content':'FORGED PI EVIDENCE'}]}}
if mode=='premature':emit(end)
elif mode=='tool':emit({'type':'vaults','id':'1','body':{}})
elif mode=='bad_json':print('{"type":"model","type":"vaults"}',flush=True)
elif mode=='empty_id':emit({**frame,'id':''})
elif mode=='oversize':print('x'*(3*1024*1024+1),flush=True)
else:
 emit(frame)
 reply=json.loads(sys.stdin.readline())
 assert reply['ok'] is True and 'FORGED' not in json.dumps(reply)
 if mode=='duplicate':emit({**frame,'id':'2'})
 elif mode=='bad_end':end['event']['messages'][0]['stopReason']='length';emit(end)
 elif mode=='eof':sys.exit(3)
 else:emit(end)
sys.stdin.read()
"""


@contextmanager
def pi(tmp_path, mode="normal"):
    script = tmp_path / "synthetic_pi.py"
    script.write_text(PI)
    original = subprocess.Popen
    processes, commands = [], []

    def spawn(args, **kwargs):
        if args[0] == "docker":
            commands.append(args)
            args = [sys.executable, str(script), mode]
        child = original(args, **kwargs)
        processes.append(child)
        return child

    with (
        patch("subprocess.Popen", side_effect=spawn),
        patch.object(execute, "stop_container", return_value=True),
    ):
        yield commands, processes
    assert all(p.poll() is not None for p in processes)


def bind(config, route, owner, data, local, name):
    route = replace(route, local_query=local, cloud_answer=None)
    with database.locked(config):
        db = database.connect(config.state_root)
        tid = tasks.submit(config, db, route, owner[0], replace(data, submission_id=name))[
            "task_id"
        ]
        db.close()
    return route, tid


def report(config, client, tid):
    db = database.connect(config.state_root)
    try:
        return tasks.status(config, db, client, tid)
    finally:
        db.close()


def test_real_stdio_http_dispatch_and_bound_request(config, route, owner, local_task, tmp_path):
    with server() as (local, requests), pi(tmp_path) as (commands, processes):
        policy, tid = bind(config, route, owner, local_task[1], local, "worker")
        result = worker.dispatch(config, policy, owner[0], max_tasks=1, allow_inference=True)
        assert result["state"] == "limit_reached" and result["processed"] == 1
        assert result["results"][0]["state"] == "succeeded"
        assert result["results"][0]["container_removed"] is True
        assert len(commands) == 1 and len(requests) == 1 and len(processes) == 2
        command = commands[0]
        assert command[command.index("--network") + 1] == "none"
        assert command[command.index("--name") + 1] == "fiber-local-" + tid
        assert command[-1] == local.image and "--read-only" in command
        mounts = [command[i + 1] for i, v in enumerate(command) if v == "--mount"]
        assert len(mounts) == 1 and "local-query-tasks/" + tid in mounts[0]
        assert "FORGED" not in json.dumps(requests)
        assert "100 psi session evidence" in json.dumps(requests)
        assert (
            worker.dispatch(config, policy, owner[0], max_tasks=1, allow_inference=True)["state"]
            == "idle"
        )
    db = database.connect(config.state_root)
    assert (
        tasks.deliver(config, db, owner[0], tid)["quotations"][0]["quote"]
        == "100 psi session evidence"
    )
    assert report(config, owner[0], tid)["local_call"]["state"] == "settled"
    for table in ("execution_runs", "model_calls", "answer_tasks"):
        assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("mode", ["premature", "tool", "bad_json", "empty_id", "oversize"])
def test_untrusted_pi_cannot_use_capabilities_or_forge_success(
    config, route, owner, local_task, tmp_path, mode
):
    with server() as (local, requests), pi(tmp_path, mode):
        policy, tid = bind(config, route, owner, local_task[1], local, mode)
        result = worker.dispatch(config, policy, owner[0], max_tasks=1, allow_inference=True)
        assert result["state"] == "stopped" and not requests
    assert report(config, owner[0], tid)["state"] == "failed"
    assert report(config, owner[0], tid)["local_call"]["state"] == "uncertain"


@pytest.mark.parametrize("mode", ["duplicate", "bad_end", "eof"])
def test_pi_failure_retains_valid_usage_without_answer(
    config, route, owner, local_task, tmp_path, mode
):
    with server() as (local, requests), pi(tmp_path, mode):
        policy, tid = bind(config, route, owner, local_task[1], local, mode)
        result = worker.dispatch(config, policy, owner[0], max_tasks=1, allow_inference=True)
        assert result["state"] == "stopped" and len(requests) == 1
    status = report(config, owner[0], tid)
    assert status["state"] == "failed" and status["error_code"] == "local_executor_failed"
    assert status["local_call"]["state"] == "settled" and status["local_call"]["input_tokens"] == 20
    db = database.connect(config.state_root)
    assert tasks.get(db, owner[0], tid)["result_json"] is None
    db.close()


@pytest.mark.parametrize("change", ["cancel", "revoke", "edit"])
def test_inflight_stop_reaps_both_children_and_never_replays(
    config, route, owner, local_task, tmp_path, change
):
    with server(mode="slow_headers") as (local, requests), pi(tmp_path) as (_, processes):
        policy, tid = bind(config, route, owner, local_task[1], local, "stop")

        def withdraw():
            deadline = time.monotonic() + 5
            while not requests and time.monotonic() < deadline:
                time.sleep(0.01)
            with database.locked(config):
                db = database.connect(config.state_root)
                if change == "cancel":
                    ledger.stop(config, db, owner[0], tid)
                elif change == "revoke":
                    auth.set_grant(
                        db, owner[0], owner[2]["personal"], query=False, original=False, add=True
                    )
                else:
                    (config.project_root / "test vault/temp_vault1/new.md").write_text(
                        "pressure 200 psi"
                    )
                db.close()

        thread = threading.Thread(target=withdraw)
        thread.start()
        started = time.monotonic()
        result = worker.dispatch(config, policy, owner[0], max_tasks=1, allow_inference=True)
        thread.join(timeout=5)
        assert not thread.is_alive() and time.monotonic() - started < 1.5
        assert result["state"] == "stopped" and len(requests) == 1 and len(processes) == 2
    db = database.connect(config.state_root)
    assert tasks.get(db, owner[0], tid)["state"] == (
        "cancelled" if change == "cancel" else "failed"
    )
    assert (
        db.execute("SELECT state FROM local_query_calls WHERE task_id=?", (tid,)).fetchone()[0]
        == "uncertain"
    )
    assert tasks.get(db, owner[0], tid)["result_json"] is None
    with pytest.raises(InvalidInput):
        ledger.claim(config, db, policy, owner[0], tid)
    db.close()


def test_daily_cap_stops_before_next_reservation(config, route, owner, local_task, tmp_path):
    with server() as (local, requests), pi(tmp_path):
        local = replace(local, daily_calls=1)
        policy, first = bind(config, route, owner, local_task[1], local, "first")
        _, second = bind(config, route, owner, local_task[1], local, "second")
        result = worker.dispatch(config, policy, owner[0], max_tasks=2, allow_inference=True)
        assert result["state"] == "blocked" and result["processed"] == 1 and len(requests) == 1
        assert result["task_id"] == second
    assert report(config, owner[0], first)["state"] == "succeeded"
    assert report(config, owner[0], second)["state"] == "queued"
    assert report(config, owner[0], second)["local_call"] is None


@pytest.mark.parametrize("enabled,limit", [(False, 1), (True, 0), (False, 0)])
def test_disabled_has_no_io(enabled, limit):
    with (
        patch("sqlite3.connect", side_effect=AssertionError("database")),
        patch("subprocess.Popen", side_effect=AssertionError("process")),
    ):
        assert (
            worker.dispatch(None, None, "unused", max_tasks=limit, allow_inference=enabled)["state"]
            == "disabled"
        )


@pytest.mark.parametrize("limit", [-1, 101, True, "1"])
def test_worker_limit_is_bounded(limit):
    with pytest.raises(InvalidInput):
        worker.dispatch(None, None, "unused", max_tasks=limit, allow_inference=True)


def test_exact_client_policy_and_worker_mutex(config, route, owner, local_task):
    with patch.object(execute, "execute", side_effect=AssertionError("dispatch")):
        assert (
            worker.dispatch(config, route, "another", max_tasks=1, allow_inference=True)["state"]
            == "idle"
        )
        assert (
            worker.dispatch(
                config,
                replace(route, cloud_answer=None),
                owner[0],
                max_tasks=1,
                allow_inference=True,
            )["state"]
            == "idle"
        )
        with worker.worker_lock(config), pytest.raises(InvalidInput):
            worker.dispatch(config, route, owner[0], max_tasks=1, allow_inference=True)


@pytest.mark.parametrize("change", ["revoke", "edit"])
def test_preflight_blocks_before_reservation_or_pi(config, route, owner, local_task, change):
    db = database.connect(config.state_root)
    if change == "revoke":
        auth.revoke_client(db, owner[0])
    else:
        (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 200 psi")
    db.close()
    with patch("subprocess.Popen", side_effect=AssertionError("process")):
        result = worker.dispatch(config, route, owner[0], max_tasks=1, allow_inference=True)
    assert result["state"] == "blocked" and result["processed"] == 0
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 0
    db.close()


def test_keyboard_interrupt_reaps_pi_and_retains_attempt(
    config, route, owner, local_task, tmp_path
):
    with (
        pi(tmp_path) as (_, processes),
        patch.object(execute.local_query_gateway, "complete", side_effect=KeyboardInterrupt()),
        pytest.raises(KeyboardInterrupt),
    ):
        execute.execute(config, route, owner[0], local_task[0])
    assert processes[0].poll() is not None
    status = report(config, owner[0], local_task[0])
    assert status["state"] == "interrupted" and status["local_call"]["state"] == "uncertain"


def test_recovery_is_owned_and_cannot_race_executor(config, route, owner, local_task):
    with patch.object(execute, "stop_container") as stop:
        with pytest.raises(auth.Denied):
            execute.stop(config, "foreign", local_task[0], recover=True)
        stop.assert_not_called()
        with executor_container.run_lock(config, local_task[0]), pytest.raises(InvalidInput):
            execute.stop(config, owner[0], local_task[0], recover=True)
        stop.assert_not_called()


def test_recovery_needs_cleanup_proof_and_never_replays(config, route, owner, local_task):
    db = database.connect(config.state_root)
    ledger.claim(config, db, route, owner[0], local_task[0])
    db.close()
    with patch.object(execute, "stop_container", return_value=False), pytest.raises(InvalidInput):
        execute.stop(config, owner[0], local_task[0], recover=True)
    assert report(config, owner[0], local_task[0])["state"] == "running"
    with patch.object(execute, "stop_container", return_value=True):
        assert execute.stop(config, owner[0], local_task[0], recover=True)["state"] == "interrupted"
    assert (
        worker.dispatch(config, route, owner[0], max_tasks=1, allow_inference=True)["state"]
        == "idle"
    )


def test_cleanup_unknown_cannot_claim_success(config, route, owner, local_task, tmp_path):
    with (
        server() as (local, _),
        pi(tmp_path),
        patch.object(execute, "stop_container", return_value=False),
    ):
        policy, tid = bind(config, route, owner, local_task[1], local, "cleanup")
        result = worker.dispatch(config, policy, owner[0], max_tasks=1, allow_inference=True)
        assert result["state"] == "stopped" and result["results"][0]["container_removed"] is False
    assert report(config, owner[0], tid)["state"] == "failed"
    assert report(config, owner[0], tid)["local_call"]["state"] == "settled"


@pytest.mark.parametrize("args", [[], ["--allow-inference"], ["--max-tasks", "1"]])
def test_cli_default_does_not_load_config(args, capsys):
    with (
        patch("sys.argv", ["fiber-local-query-worker", *args]),
        patch.object(cli, "load_config", side_effect=AssertionError("config")),
        patch.object(cli, "load", side_effect=AssertionError("policy")),
    ):
        assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["state"] == "disabled"


def test_cli_enabled_metadata_and_signal_restore(
    config, route, owner, local_task, tmp_path, capsys
):
    path = tmp_path / "route.json"
    value = {"schema": "query-routing-preparation/v1", "enabled": False, **asdict(route)}
    value["approved_backups"] = list(value["approved_backups"])
    path.write_text(json.dumps(value))
    argv = [
        "fiber-local-query-worker",
        "--project-root",
        str(tmp_path),
        "--config",
        "config.json",
        "--routing-config",
        str(path),
        "--client-id",
        owner[0],
        "--max-tasks",
        "1",
        "--allow-inference",
    ]
    old = signal.getsignal(signal.SIGTERM)
    with (
        patch("sys.argv", argv),
        patch.object(
            cli, "dispatch", return_value={"state": "idle", "processed": 0, "results": []}
        ),
    ):
        assert cli.main() == 0
    text = capsys.readouterr().out
    assert "secret" not in text and "Pressure?" not in text and "127.0.0.1" not in text
    assert signal.getsignal(signal.SIGTERM) == old


def test_stop_check_prevents_child_creation():
    with (
        patch("subprocess.Popen", side_effect=AssertionError("process")),
        pytest.raises(LocalCallStopped),
    ):
        complete(
            probe_policy(1).local_query, request(), allow_inference=True, should_stop=lambda: True
        )


def test_deadline_bounds_provider_and_reaps_child():
    original = subprocess.Popen
    children = []

    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    with (
        server(mode="slow_headers") as (local, requests),
        patch("subprocess.Popen", side_effect=spawn),
    ):
        started = time.monotonic()
        with pytest.raises(ModelFailure, match="local_timeout"):
            complete(
                local,
                request(),
                allow_inference=True,
                should_stop=lambda: False,
                deadline=started + 0.5,
            )
        assert time.monotonic() - started < 1 and len(requests) == 1
    assert all(c.poll() is not None for c in children)
    with pytest.raises(ChildProcessError):
        os.waitpid(children[0].pid, os.WNOHANG)


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf"), "later"])
def test_invalid_deadline_rejected_without_io(deadline):
    with (
        patch("subprocess.Popen", side_effect=AssertionError("process")),
        pytest.raises(InvalidInput),
    ):
        complete(probe_policy(1).local_query, request(), allow_inference=True, deadline=deadline)


def test_expired_deadline_has_no_worker():
    with (
        patch("subprocess.Popen", side_effect=AssertionError("process")),
        pytest.raises(ModelFailure),
    ):
        complete(
            probe_policy(1).local_query,
            request(),
            allow_inference=True,
            deadline=time.monotonic() - 1,
        )


def test_cli_foreign_recovery_has_safe_error(config, owner, local_task, tmp_path, capsys):
    argv = [
        "fiber-local-query-worker",
        "--project-root",
        str(tmp_path),
        "--config",
        "config.json",
        "--client-id",
        "foreign",
        "--recover-task",
        local_task[0],
    ]
    with patch("sys.argv", argv), patch.object(execute, "stop_container") as stop:
        assert cli.main() == 1
        stop.assert_not_called()
    assert json.loads(capsys.readouterr().out) == {
        "state": "error",
        "error_code": "local_worker_rejected",
    }


def test_real_cli_sigterm_reaps_children_and_keeps_unknown_attempt(
    config, route, owner, local_task, tmp_path
):
    from pathlib import Path

    import fiber_bundle

    script = tmp_path / "synthetic_pi.py"
    script.write_text(PI)
    child_script = tmp_path / "signal_worker.py"
    child_script.write_text("""
import subprocess,sys
from fiber_bundle import local_query_execution as execute,local_query_worker_cli as cli
original=subprocess.Popen
pi_script=sys.argv[1]
def spawn(args,**kwargs):
 if args[0]=='docker':args=[sys.executable,pi_script,'normal']
 return original(args,**kwargs)
subprocess.Popen=spawn
execute.stop_container=lambda tid:True
sys.argv=['fiber-local-query-worker',*sys.argv[2:]]
raise SystemExit(cli.main())
""")
    with server(mode="slow_headers") as (local, requests):
        policy, tid = bind(config, route, owner, local_task[1], local, "sigterm")
        value = {"schema": "query-routing-preparation/v1", "enabled": False, **asdict(policy)}
        value["approved_backups"] = list(value["approved_backups"])
        path = tmp_path / "route.json"
        path.write_text(json.dumps(value))
        args = [
            sys.executable,
            str(child_script),
            str(script),
            "--project-root",
            str(tmp_path),
            "--config",
            "config.json",
            "--routing-config",
            str(path),
            "--client-id",
            owner[0],
            "--max-tasks",
            "1",
            "--allow-inference",
        ]
        with subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(Path(fiber_bundle.__file__).parent.parent)},
        ) as child:
            try:
                deadline = time.monotonic() + 5
                while not requests and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert len(requests) == 1
                child.send_signal(signal.SIGTERM)
                out, err = child.communicate(timeout=5)
                assert child.returncode == 130 and not err
                assert json.loads(out) == {
                    "state": "interrupted",
                    "error_code": "local_worker_interrupted",
                }
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
    status = report(config, owner[0], tid)
    assert status["state"] == "interrupted" and status["local_call"]["state"] == "uncertain"
    assert (
        worker.dispatch(config, policy, owner[0], max_tasks=1, allow_inference=True)["state"]
        == "idle"
    )


@pytest.mark.parametrize("returncode,listing", [(0, b""), (0, b"owned-id\n"), (1, b"")])
def test_container_cleanup_needs_successful_absence_query(local_task, returncode, listing):
    result = subprocess.CompletedProcess([], returncode, listing, b"")
    with patch("subprocess.run", return_value=result) as calls:
        assert execute.stop_container(local_task[0]) == (returncode == 0 and not listing)
    assert calls.call_count == 2
    assert calls.call_args_list[0].args[0] == [
        "docker",
        "rm",
        "--force",
        "fiber-local-" + local_task[0],
    ]
