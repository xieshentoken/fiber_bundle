import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest
from test_cloud_answer_selection import route as route
from test_cloud_answer_tasks import cloud_task as cloud_task
from test_local_query_tasks import local_task as local_task
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import cloud_answer_selection as selection
from fiber_bundle import cloud_answer_switch_cli as cli
from fiber_bundle import database


@pytest.mark.parametrize("args", [[], ["--backup-index", "1"], ["--run-id", "invalid"]])
def test_default_off_never_reads_files(args, capsys):
    with (
        patch("sys.argv", ["fiber-cloud-answer-switch", *args]),
        patch.object(cli, "load_config", side_effect=AssertionError("IO")),
    ):
        assert cli.main() == 0
    assert json.loads(capsys.readouterr().out) == {"state": "disabled", "model_calls": 0}


@pytest.mark.parametrize(
    "args",
    [
        ["--approve-backup"],
        ["--approve-backup", "--backup-index", "0"],
        ["--approve-backup", "--backup-index", "5"],
    ],
)
def test_bad_args_are_content_free(args, capsys):
    with patch("sys.argv", ["fiber-cloud-answer-switch", *args]):
        assert cli.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "state": "error",
        "error_code": "cloud_selection_rejected",
    }


def test_real_cli_selects_and_persists_without_model_or_credential_access(
    config, route, owner, cloud_task, tmp_path
):
    import fiber_bundle

    path = tmp_path / "routing.json"
    value = {"schema": "query-routing-preparation/v1", "enabled": False, **asdict(route)}
    value["approved_backups"] = list(value["approved_backups"])
    path.write_text(json.dumps(value))
    args = [
        sys.executable,
        "-m",
        "fiber_bundle.cloud_answer_switch_cli",
        "--project-root",
        str(tmp_path),
        "--config",
        "config.json",
        "--routing-config",
        str(path),
        "--client-id",
        owner[0],
        "--run-id",
        cloud_task,
        "--backup-index",
        "2",
        "--selection-id",
        "explicit-host-1",
        "--approve-backup",
    ]
    env = {**os.environ, "PYTHONPATH": str(Path(fiber_bundle.__file__).parent.parent)}
    first = subprocess.run(args, capture_output=True, text=True, timeout=5, env=env)
    assert (
        first.returncode == 0
        and not first.stderr
        and json.loads(first.stdout)["state"] == "selected"
    )
    second = subprocess.run(args, capture_output=True, text=True, timeout=5, env=env)
    assert second.returncode == 0 and first.stdout == second.stdout
    db = database.connect(config.state_root)
    assert len(selection.report(db, owner[0], cloud_task)) == 1
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    assert not Path(route.answer_policy(2).secret_file).exists()
    db.close()
