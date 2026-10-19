"""Opt-in actual host reads real compiler and ffmpeg-produced publications."""

import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest
from test_video_v2 import clip as clip

from fiber_bundle import auth, database, video_runner, video_tools
from fiber_bundle.agent_connections import access_skill, application, configuration, store
from fiber_bundle.agent_connections.contracts import ConnectionSpec

pytest_plugins = ["test_source_imports", "test_execution"]


@pytest.fixture(autouse=True)
def explicit_tasks(monkeypatch):
    monkeypatch.setattr("fiber_bundle.automatic_ingestion.Runtime.start", lambda self: None)


def test_installed_host_skill_expert_and_video(
    network, config, owner, policy, clip, monkeypatch, tmp_path
):
    installation = os.environ.get("FIBER_HERMES_CHECKOUT")
    if not installation or not os.environ.get("FIBER_EXPERT_RUNTIME"):
        pytest.skip("opt-in installed Hermes and fixed compiler required")
    import test_video_v2
    from test_expert_authoring import test_real_native_compiler_pi_proposals_and_draft_end_to_end
    from test_video_v2 import actual_tool, setup

    from fiber_bundle import source_processing_policy

    original_request = test_video_v2.import_request

    def current_request(*args, **kwargs):
        value = original_request(*args, **kwargs)
        value["configuration_sha256"] = source_processing_policy.inspect(config, None)[
            "configuration_sha256"
        ]
        return value

    monkeypatch.setattr(test_video_v2, "import_request", current_request)

    test_real_native_compiler_pi_proposals_and_draft_end_to_end(
        config, owner, policy, monkeypatch, True, True, False
    )
    cid, headers, ids = owner
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, ids["supplier"], query=True, original=True, add=True)
    _, _, video = setup(
        config, (cid, headers, {"personal": ids["supplier"]}), clip, check=False, direct=True
    )
    monkeypatch.setattr(video_tools, "execute", actual_tool)
    assert video_runner.run(config, cid, video["id"])["state"] == "published"
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    with database.connect(config.state_root) as db:
        from fiber_bundle import expert_evidence

        task = db.execute(
            "SELECT task_id FROM source_expert_publications WHERE state='published'"
        ).fetchone()[0]
        packet = expert_evidence.describe(config, db, cid, task)
        assert packet["module_id"] == "expert:" + task
        assert packet["source_version_id"] and packet["source_sha256"]
        assert packet["publication"]["completeness_status"] == "passed"
        assert packet["knowledge_verified"] is False
        item = configuration.create(
            config,
            db,
            ConnectionSpec(
                schema_version="fiber-agent-connection/v2",
                name="Host fixture",
                agent_type="mcp",
                vault_ids=list(ids.values()),
            ),
            "host-create",
        )
        application.apply(config, db, store.get(db, item["id"]), str(network.base_url).rstrip("/"))
        configuration.control(config, db, item["id"], "enable", 1, "host-enable")
        raw = access_skill.package(access_skill.material(config, db, item["id"]))
        export = application.exported(config, store.get(db, item["id"]))
        calls = db.execute("SELECT count(*) FROM model_calls").fetchone()[0]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        archive.extractall(profile / "skills")
    (profile / "config.yaml").write_text(json.dumps({"mcp_servers": export["mcpServers"]}))
    checkout = Path(installation)
    process = subprocess.run(
        [
            str(checkout / "venv/bin/python"),
            "-I",
            "-B",
            str(Path(__file__).with_name("knowledge_host_probe.py")),
            str(checkout),
            str(tmp_path),
        ],
        env={
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
            "HERMES_HOME": str(profile),
            "TMPDIR": str(tmp_path),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        },
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
    )
    assert process.returncode == 0, process.stderr.decode()[-4000:]
    report = json.loads((tmp_path / "host-result.json").read_text())
    assert report["state"] == "passed" and report["model_called"] is False
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == calls
    if output := os.environ.get("FIBER_AGENT_HOST_REPORT"):
        Path(output).write_text(json.dumps(report, indent=2))
