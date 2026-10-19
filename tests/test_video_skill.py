"""Real V3 initialization and terminology adoption without network or model calls."""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fiber_bundle import video_skill
from fiber_bundle.config import InvalidInput, make_directory
from fiber_bundle.inputs import canonical
from fiber_bundle.query_policy_store import write_private


def configured(config, tmp_path):
    original = Path(__file__).parents[1] / "skills/bili-note-V3"
    copied = tmp_path / "skill"
    shutil.copytree(original, copied)
    make_directory(config.state_root / "configs")
    write_private(
        config.state_root / "configs/video-skill.json",
        canonical(
            video_skill.Settings(skill_path=str(copied), python_path=sys.executable).model_dump()
        ).encode(),
    )
    return copied


def test_real_v3_job_uses_frozen_default_terms_and_application_adoption(config, tmp_path):
    copied = configured(config, tmp_path)
    frozen = video_skill.freeze(config)
    job = config.state_root / "video-test"
    video_skill.run(
        config,
        frozen,
        job,
        "init_job",
        ["https://www.bilibili.com/video/BV1XC4y197GR/", "--out", str(job)],
        lambda: None,
    )
    video_skill.adopt_terms(config, frozen, job, lambda: None)
    glossary = json.loads((job / "terms/glossary.json").read_bytes())
    assert glossary["confirmed"] and glossary["confirmed_by"] == "fiber-application-policy"
    assert [t["canonical"] for t in glossary["terms"]] == list(video_skill.DEFAULT_TERMS)
    context = json.loads((job / "terms/asr_context.json").read_bytes())
    assert context["confirmed"]
    (copied / "SKILL.md").write_text("changed source")
    assert video_skill.verify(config, frozen).is_dir()
    assert video_skill.freeze(config)["sha256"] != frozen["sha256"]
    snapshot = video_skill.verify(config, frozen)
    (snapshot / "SKILL.md").write_text("changed snapshot")
    with pytest.raises(InvalidInput, match="video_skill_changed"):
        video_skill.verify(config, frozen)


def test_script_allowlist_and_cancel_stop_owned_subprocess(config, tmp_path):
    configured(config, tmp_path)
    frozen = video_skill.freeze(config)
    job = config.state_root / "video-test"
    with pytest.raises(InvalidInput, match="script_not_allowed"):
        video_skill.run(config, frozen, job, "publish_note", [], lambda: None)
    with pytest.raises(InvalidInput, match="cancelled"):
        video_skill.run(
            config,
            frozen,
            job,
            "init_job",
            [],
            lambda: (_ for _ in ()).throw(InvalidInput("cancelled")),
        )
    assert not job.exists()


def test_configured_tool_directories_work_under_minimal_launcher_path(
    config, tmp_path, monkeypatch
):
    from pydantic import ValidationError

    tools = tmp_path / "tools"
    tools.mkdir()
    tool = tools / "ffprobe"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o700)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    configured(config, tmp_path)
    value = video_skill.settings(config)
    value["tool_directories"] = [str(tools)]
    write_private(config.state_root / "configs/video-skill.json", canonical(value).encode())
    assert video_skill.inspect(config)["tools"]["ffprobe"]
    assert video_skill.tool_path(value).split(":")[0] == str(tools)
    with pytest.raises(ValidationError):
        video_skill.Settings(tool_directories=["relative"])


def test_video_skill_settings_reads_do_not_install_or_run(config, http, tmp_path):
    response = http.get("/admin/video-processing/skill")
    assert response.status_code == 200
    assert response.json()["automatic_asr"]
    assert not (config.state_root / "video-skills").exists()
    assert not (config.state_root / "video-skill-jobs").exists()
    copied = configured(config, tmp_path)
    snapshot = http.get("/admin/video-processing/skill").json()
    changed = {**snapshot["settings"], "default_terms": ["ASR", "API"]}
    response = http.put(
        "/admin/video-processing/skill",
        json={"settings": changed, "expected_sha256": snapshot["sha256"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["skill_path"] == str(copied)
    assert response.json()["settings"]["default_terms"] == ["ASR", "API"]
    assert not (config.state_root / "video-skills").exists()


def test_script_owner_stops_child_when_parent_pipe_is_closed(tmp_path):
    marker = tmp_path / "child.pid"
    stopped = tmp_path / "child.stopped"
    code = (
        "import os,time,signal,sys;from pathlib import Path;"
        "signal.signal(signal.SIGTERM,lambda *a:(Path("
        + repr(str(stopped))
        + ").write_text('stopped'),sys.exit(0)));Path("
        + repr(str(marker))
        + ").write_text(str(os.getpid()));time.sleep(30)"
    )
    owner = Path(video_skill.__file__).with_name("video_skill_process.py")
    process = subprocess.Popen(
        [sys.executable, str(owner), canonical([sys.executable, "-c", code])],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        process.stdin.close()
        assert process.wait(timeout=5) != 0
        assert stopped.read_text() == "stopped"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_empty_asr_has_finite_reason_without_leaking_stderr(config, tmp_path):
    source = configured(config, tmp_path)
    (source / "scripts/run_asr.py").write_text(
        "import sys;sys.stderr.write('sensitive diagnostic should stay private\\n"
        "Qwen3-ASR returned no transcript text\\n');sys.exit(2)"
    )
    frozen = video_skill.freeze(config)
    with pytest.raises(InvalidInput) as error:
        video_skill.run(config, frozen, config.state_root / "job", "run_asr", [], lambda: None)
    assert str(error.value) == "video_skill_asr_empty"
    assert "sensitive" not in str(error.value)
