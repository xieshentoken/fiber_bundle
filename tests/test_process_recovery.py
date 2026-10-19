"""Real subprocess exits at storage boundaries; never calls production services."""

import json
import subprocess
import sys

import pytest

from fiber_bundle import database
from fiber_bundle.store import Store

WORKER = r"""
import os
import sys
from pathlib import Path
from fiber_bundle import objects
from fiber_bundle.config import load_config
from fiber_bundle.inputs import AssetInput
from fiber_bundle.store import Store
root = Path(sys.argv[1])
mode = sys.argv[2]
config = load_config(root / "config.json", root)
if mode == "mid_write":
    def interrupted(fd, data):
        os.write(fd, data[:2])
        os.fsync(fd)
        os._exit(73)
    objects._write_bytes = interrupted
elif mode == "before_commit":
    def interrupted(*args):
        os._exit(73)
    Store._commit_version = interrupted
elif mode == "after_commit":
    original = Store._commit_version
    def interrupted(*args):
        original(*args)
        os._exit(73)
    Store._commit_version = interrupted
store = Store(config)
r = store.ingest(ingress_scope="worker", event_id="1", vault="personal",
                 source_key="worker-source", raw_event=b"synthetic event",
                 items=(AssetInput(b"synthetic body", "body.txt", "text/plain", "body"),),
                 session_scope="worker", session_id="1")
print(r.job_id)
"""


@pytest.mark.parametrize(
    "stage,initial,recovered",
    [
        ("mid_write", "receiving", "incomplete"),
        ("before_commit", "receiving", "stored"),
        ("after_commit", "stored", "stored"),
    ],
)
def test_killed_process_recovery(store, config, stage, initial, recovered):
    child = subprocess.run(
        [sys.executable, "-c", WORKER, str(config.project_root), stage],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert child.returncode == 73, child.stderr
    connection = database.connect(config.state_root)
    row = connection.execute("SELECT * FROM receipts").fetchone()
    connection.close()
    assert row["state"] == initial
    checked = Store(config).recover()
    assert checked[0].job_id == row["job_id"] and checked[0].state == recovered
    resumed = subprocess.run(
        [sys.executable, "-c", WORKER, str(config.project_root), "normal"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.strip() == row["job_id"]
    assert Store(config).recover()[0].state == "stored"


def test_two_processes_replay_same_event_once(store, config):
    children = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, str(config.project_root), "normal"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = []
    for child in children:
        out, err = child.communicate(timeout=20)
        assert child.returncode == 0, err
        results.append(out.strip())
    assert results[0] == results[1]
    connection = database.connect(config.state_root)
    try:
        assert connection.execute("SELECT count(*) FROM receipts").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM source_versions").fetchone()[0] == 1
    finally:
        connection.close()


def test_cli_explicit_init_reopen_and_snapshot(config):
    prefix = [sys.executable, "-m", "fiber_bundle.cli"]
    flags = ["--project-root", str(config.project_root), "--config", "config.json"]
    failed = subprocess.run(prefix + ["check"] + flags, capture_output=True, text=True, timeout=20)
    assert failed.returncode == 1
    assert not config.state_root.exists()
    initialized = subprocess.run(
        prefix + ["init"] + flags, capture_output=True, text=True, timeout=20
    )
    assert initialized.returncode == 0, initialized.stderr
    assert json.loads(initialized.stdout)["status"] == "initialized"
    checked = subprocess.run(prefix + ["check"] + flags, capture_output=True, text=True, timeout=20)
    assert checked.returncode == 0 and json.loads(checked.stdout) == {"receipts": []}
    snapshot = subprocess.run(
        prefix + ["snapshot"] + flags, capture_output=True, text=True, timeout=20
    )
    assert snapshot.returncode == 0
    assert json.loads(snapshot.stdout)["full_system_backup"] is False
