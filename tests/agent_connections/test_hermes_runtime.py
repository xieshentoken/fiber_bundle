import json
import os
import subprocess
from pathlib import Path

import pytest


def test_installed_hermes_entrypoint_uses_only_isolated_profile_and_local_stub(tmp_path):
    installation = os.environ.get("FIBER_HERMES_CHECKOUT")
    if not installation:
        pytest.skip("opt-in installed Hermes probe: set FIBER_HERMES_CHECKOUT")
    checkout = Path(installation).resolve()
    project = Path(__file__).resolve().parents[2]
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    (profile / "config.yaml").write_text(
        json.dumps(
            {
                "agent": {"api_max_retries": 1},
                "compression": {"enabled": False},
                "memory": {"memory_enabled": False, "user_profile_enabled": False},
            }
        )
    )
    process = subprocess.run(
        [
            str(checkout / "venv/bin/python"),
            "-I",
            "-B",
            str(Path(__file__).with_name("hermes_probe.py")),
            str(checkout),
            str(project),
            str(tmp_path),
        ],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
            "HERMES_HOME": str(profile),
            "TMPDIR": str(tmp_path),
        },
        capture_output=True,
        timeout=60,
    )
    # All content in this subprocess is synthetic. Never pass a personal config into this probe.
    assert process.returncode == 0, process.stderr.decode()[-6000:]
    report = json.loads((tmp_path / "probe-result.json").read_text())
    assert report["state"] == "passed" and report["real_model_called"] is False
    assert report["checks"]["success"]["provider_calls"] == 1
    output = os.environ.get("FIBER_AGENT_PROBE_REPORT")
    if output:
        Path(output).write_text(json.dumps(report, indent=2) + "\n")
