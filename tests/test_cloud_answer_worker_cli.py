import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest
from test_cloud_answer_tasks import cloud_task as cloud_task
from test_cloud_answer_worker import report
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import cloud_answer_gateway as gateway
from fiber_bundle import cloud_answer_worker as worker
from fiber_bundle import cloud_answer_worker_cli as cli


@pytest.mark.parametrize(
    "argv",
    [[], ["--allow-inference"], ["--max-tasks", "1"], ["--allow-inference", "--max-tasks", "0"]],
)
def test_cli_disabled_does_not_read_configuration(argv, capsys):
    old = signal.getsignal(signal.SIGTERM)
    with (
        patch("sys.argv", ["fiber-cloud-answer-worker", *argv]),
        patch.object(cli, "load_config", side_effect=AssertionError("config")),
    ):
        assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["state"] == "disabled"
    assert signal.getsignal(signal.SIGTERM) == old


@pytest.mark.parametrize(
    "argv",
    [
        ["--max-tasks", "-1"],
        ["--max-tasks", "101"],
        ["--allow-inference", "--max-tasks", "1"],
        ["--cancel-run", "foreign"],
    ],
)
def test_cli_rejection_is_content_free(argv, capsys):
    with patch("sys.argv", ["fiber-cloud-answer-worker", *argv]):
        assert cli.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "state": "error",
        "error_code": "cloud_worker_rejected",
    }


@pytest.mark.parametrize("mode", ["cancel", "recover"])
def test_cli_owned_cleanup_never_reads_route_or_calls_model(
    config, owner, cloud_task, tmp_path, capsys, mode
):
    args = [
        "fiber-cloud-answer-worker",
        "--project-root",
        str(tmp_path),
        "--config",
        "config.json",
        "--client-id",
        owner[0],
        "--" + mode + "-run",
        cloud_task,
    ]
    with (
        patch("sys.argv", args),
        patch.object(cli, "load", side_effect=AssertionError("route")),
        patch.object(gateway, "complete", side_effect=AssertionError("model")),
    ):
        assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["state"] == (
        "cancelled" if mode == "cancel" else "queued"
    )


def test_cli_foreign_cleanup_is_refused(config, cloud_task, tmp_path, capsys):
    with patch(
        "sys.argv",
        [
            "fiber-cloud-answer-worker",
            "--project-root",
            str(tmp_path),
            "--config",
            "config.json",
            "--client-id",
            "foreign",
            "--recover-run",
            cloud_task,
        ],
    ):
        assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "cloud_worker_rejected"


def test_real_cli_sigterm_stops_credential_group_and_keeps_unknown_fee(
    config, route, owner, cloud_task, tmp_path
):
    import fiber_bundle

    marker = tmp_path / "credential-processes.json"
    credential = tmp_path / "slow_credential.py"
    credential.write_text("""
import json,os,subprocess,sys,time
from pathlib import Path
sys.stdin.buffer.read()
p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
Path(sys.argv[1]).write_text(json.dumps({'child':os.getpid(),'descendant':p.pid,'pgid':os.getpgrp()}))
time.sleep(30)
""")
    wrapper = tmp_path / "cli_wrapper.py"
    wrapper.write_text("""
import subprocess,sys,time
from pathlib import Path
from fiber_bundle import cloud_answer_worker_cli as cli
from fiber_bundle import cloud_answer_tasks as tasks
original=subprocess.Popen
script,marker=sys.argv[1:3]
original_stop=tasks.stop
def stop(*args,**kwargs):
 if kwargs.get('interrupt'):
  Path(marker+'.cleanup').touch()
  time.sleep(.15)
 return original_stop(*args,**kwargs)
tasks.stop=stop
def spawn(args,**kwargs):
 if len(args)>2 and args[1:3]==['-m','fiber_bundle.cloud_answer_gateway']:
  args=[sys.executable,script,marker]
 return original(args,**kwargs)
subprocess.Popen=spawn
sys.argv=['fiber-cloud-answer-worker',*sys.argv[3:]]
raise SystemExit(cli.main())
""")
    value = {"schema": "query-routing-preparation/v1", "enabled": False, **asdict(route)}
    value["approved_backups"] = list(value["approved_backups"])
    path = tmp_path / "route.json"
    path.write_text(json.dumps(value))
    args = [
        sys.executable,
        str(wrapper),
        str(credential),
        str(marker),
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
    ) as process:
        cleaned = False
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert marker.exists()
            children = json.loads(marker.read_text())
            assert children["pgid"] == children["child"]
            process.send_signal(signal.SIGTERM)
            deadline = time.monotonic() + 5
            while not Path(str(marker) + ".cleanup").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert Path(str(marker) + ".cleanup").exists()
            process.send_signal(signal.SIGTERM)
            out, err = process.communicate(timeout=5)
            assert process.returncode == 130 and not err
            assert json.loads(out) == {
                "state": "interrupted",
                "error_code": "cloud_worker_interrupted",
            }
            for pid in (children["child"], children["descendant"]):
                deadline = time.monotonic() + 2
                while True:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    assert time.monotonic() < deadline, "owned child still exists"
                    time.sleep(0.01)
            cleaned = True
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if marker.exists() and not cleaned:
                try:
                    os.killpg(json.loads(marker.read_text())["pgid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
    status = report(config, owner, cloud_task)
    assert status["state"] == "interrupted" and status["cloud_call"]["state"] == "uncertain"
    assert (
        worker.dispatch(config, route, owner[0], max_tasks=1, allow_inference=True)["state"]
        == "idle"
    )
