"""Storage/environment round trips across the launch boundary, without provider inference."""

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_jobs import job as job
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import database, query_job_launch, query_jobs
from fiber_bundle.config import InvalidInput, load_config
from fiber_bundle.runtime_environment import child_environment

PROBE = """
import json,sys
from pathlib import Path
from fiber_bundle.config import load_config,StorageError
from fiber_bundle.cloud_files import require_available
try:
    c=load_config(Path(sys.argv[1]),Path(sys.argv[2]))
    p=c.vault_path(dict(c.vaults)['personal'])
    require_available(p,tree=True)
    print(json.dumps({'root':str(p),'note':(p/'note.md').read_text()}))
except StorageError as e:
    print(json.dumps({'error':str(e)}))
"""


@pytest.mark.parametrize("cloud", [False, True])
def test_real_child_reads_only_mapped_vault_and_pinned_helper(
    config, owner, job, tmp_path, monkeypatch, cloud
):
    external = tmp_path / ("Mobile Documents" if cloud else "external")
    external.mkdir()
    (external / "note.md").write_text("synthetic external source")
    (config.project_root / dict(config.vaults)["personal"] / "note.md").write_text("local impostor")
    helper = tmp_path / "cloud-helper"
    helper.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        'print(\'{"schema":"cloud-status/v1","available":true}\')\n'
    )
    helper.chmod(0o700)
    monkeypatch.setenv("FIBER_CLOUD_HELPER", str(helper))
    monkeypatch.setenv("FIBER_CLOUD_HELPER_SHA256", hashlib.sha256(helper.read_bytes()).hexdigest())
    monkeypatch.setenv("PRIVATE_PROVIDER_KEY", "synthetic-env-sentinel")
    mapped = replace(config, vault_roots=(("personal", str(external)),))
    captured = {}
    real_popen = subprocess.Popen

    def probe(command, environment):
        with patch.object(subprocess, "Popen", real_popen):
            result = subprocess.run(
                [command[0], "-c", PROBE, command[6], command[4]],
                env=environment,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        return json.loads(result.stdout)

    def spawn(command, **kwargs):
        captured.update(command=command, environment=kwargs["env"])
        # Execute a finite read-only probe using precisely the serialized config and env.
        assert probe(command, kwargs["env"]) == {
            "root": str(external),
            "note": "synthetic external source",
        }
        return SimpleNamespace(wait=lambda: 0)

    with patch.object(query_job_launch.subprocess, "Popen", side_effect=spawn) as launch:
        query_job_launch.launch(mapped, owner[0], job["id"])
        query_job_launch.launch(mapped, owner[0], job["id"])
        assert launch.call_count == 1
    if cloud:
        environment = dict(captured["environment"])
        del environment["FIBER_CLOUD_HELPER"]
        assert probe(captured["command"], environment) == {"error": "cloud_status_helper_required"}
        helper.write_text(helper.read_text() + "# changed\n")
        assert probe(captured["command"], captured["environment"]) == {
            "error": "cloud_status_helper_changed"
        }
    assert "PRIVATE_PROVIDER_KEY" not in captured["environment"]
    loaded = load_config(config.state_root / "query-runner-config.json", config.project_root)
    assert loaded == mapped
    assert (config.state_root / "query-runner-config.json").stat().st_mode & 0o777 == 0o600
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_worker_environment_preserves_portable_runtime_and_omits_user_environment(monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/synthetic/portable-runtime")
    monkeypatch.setenv("PYTHONPATH", "/synthetic/user-packages")
    monkeypatch.setenv("PRIVATE_PROVIDER_KEY", "synthetic-env-sentinel")
    value = child_environment()
    assert value["PYTHONHOME"] == "/synthetic/portable-runtime"
    assert value["PYTHONPATH"] != "/synthetic/user-packages"
    assert value["PYTHONNOUSERSITE"] == value["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "PRIVATE_PROVIDER_KEY" not in value


def test_changed_external_root_rejects_launch_and_cannot_replay(config, owner, job, tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    mapped = replace(config, vault_roots=(("personal", str(external)),))
    mapped.validate()
    external.rmdir()
    external.symlink_to(config.project_root / dict(config.vaults)["personal"])
    with patch.object(query_job_launch.subprocess, "Popen") as launch:
        with pytest.raises(InvalidInput, match="unavailable"):
            query_job_launch.launch(mapped, owner[0], job["id"])
        query_job_launch.launch(mapped, owner[0], job["id"])
        launch.assert_not_called()
    db = database.connect(config.state_root)
    assert query_jobs.view(db, owner[0], job["id"])["state"] == "interrupted"
    db.close()
