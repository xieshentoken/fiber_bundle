import json
import subprocess
import sys
from uuid import uuid4

import pytest
from test_media_candidates import PROBE, RAW, SOURCE, SPEC

from fiber_bundle import media_files, media_subtitles
from fiber_bundle import media_worker as worker
from fiber_bundle.config import InvalidInput
from fiber_bundle.media_candidate import candidate

POLICY = worker.MediaPolicy("sha256:" + "a" * 64, 1)


@pytest.mark.parametrize("image", ["latest", "python:3.13", "sha256:abc"])
def test_requires_immutable_image(image):
    with pytest.raises(InvalidInput):
        worker.MediaPolicy(image).validate()


def test_only_private_mounts_and_bounded_offline_runtime(tmp_path):
    inputs, outputs = tmp_path / "input", tmp_path / "output"
    inputs.mkdir()
    outputs.mkdir()
    args = worker.command(POLICY, inputs, outputs, str(uuid4()))
    assert args.count("--mount") == 2
    assert f"type=bind,source={inputs},target=/media/input,readonly" in args
    assert f"type=bind,source={outputs},target=/media/output" in args
    for key, value in [
        ("--user", "1000:1000"),
        ("--network", "none"),
        ("--memory", "512m"),
        ("--memory-swap", "512m"),
        ("--pids-limit", "32"),
        ("--cpus", "1"),
        ("--cap-drop", "ALL"),
        ("--log-driver", "none"),
    ]:
        assert args[args.index(key) + 1] == value
    assert "--read-only" in args and "no-new-privileges" in args
    assert not {"--env", "--publish", "--privileged", "/var/run/docker.sock"} & set(args)


def child(monkeypatch, config, *, sleeping=False, invalid=False):
    created, removed, launched = [], [], []
    real_popen = subprocess.Popen
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda *a, **kw: created.append(a[0]) or subprocess.CompletedProcess(a[0], 0),
    )
    monkeypatch.setattr(worker, "remove", removed.append)

    def start(args, **kw):
        launched.append(args)
        identifier = args[-1].removeprefix("fiber-media-")
        output = worker.directory(config, identifier) / "output"
        value = candidate(SOURCE, RAW, SPEC, PROBE)
        raw = b"{}" if invalid else media_files.canonical(value)
        normalized = media_subtitles.normalized(value["subtitle"]["cues"])
        code = "import time;from pathlib import Path;"
        if sleeping:
            code += "time.sleep(5);"
        code += f"Path({str(output / 'normalized.srt')!r}).write_bytes({normalized!r});"
        code += f"Path({str(output / 'result.json')!r}).write_bytes({raw!r})"
        return real_popen([sys.executable, "-c", code], **kw)

    monkeypatch.setattr(worker.subprocess, "Popen", start)
    return created, removed, launched


def execute(config, identifier, guard=lambda: None):
    return worker.execute(config, POLICY, identifier, SOURCE, RAW, SPEC, guard=guard)


def test_real_child_no_replay_and_original_retained(config, monkeypatch):
    created, removed, launched = child(monkeypatch, config)
    identifier = str(uuid4())
    value = execute(config, identifier)
    assert len(created) == len(removed) == len(launched) == 1
    assert value["state"] == "candidate"
    assert (worker.directory(config, identifier) / "input/source").read_bytes() == SOURCE
    with pytest.raises(InvalidInput, match="attempt_exists"):
        execute(config, identifier)
    assert len(created) == 1


@pytest.mark.parametrize("failure", ["invalid", "timeout", "revoke", "cancel", "snapshot"])
def test_failed_attempt_stops_writer_keeps_original(config, monkeypatch, failure):
    created, removed, launched = child(
        monkeypatch, config, sleeping=failure == "timeout", invalid=failure == "invalid"
    )
    identifier = str(uuid4())

    def guard():
        root = worker.directory(config, identifier)
        if failure in {"revoke", "cancel"} and launched:
            raise PermissionError("synthetic stop")
        if failure == "snapshot" and (root / "output/result.json").exists():
            (root / "input/source").write_bytes(b"changed")

    with pytest.raises((InvalidInput, PermissionError)):
        execute(config, identifier, guard)
    assert len(created) == len(removed) == len(launched) == 1
    if failure != "snapshot":
        assert (worker.directory(config, identifier) / "input/source").read_bytes() == SOURCE


def test_denied_before_snapshot_no_files(config):
    def guard():
        raise PermissionError("synthetic denial")

    with pytest.raises(PermissionError):
        execute(config, str(uuid4()), guard)
    assert not config.state_root.exists()


def test_create_failure_does_not_remove_foreign_worker(config, monkeypatch):
    removed = []

    def fail(*a, **kw):
        raise subprocess.CalledProcessError(1, "create")

    monkeypatch.setattr(worker.subprocess, "run", fail)
    monkeypatch.setattr(worker, "remove", removed.append)
    with pytest.raises(subprocess.CalledProcessError):
        execute(config, str(uuid4()))
    assert removed == []


@pytest.mark.parametrize("change", ["label", "image", "mount", "owned"])
def test_recovery_only_stops_owned_container(config, monkeypatch, change):
    identifier = str(uuid4())
    root = worker.directory(config, identifier)
    info = {
        "Config": {"Labels": {"fiber-bundle.media": identifier}, "Image": POLICY.image},
        "Mounts": [
            {"Destination": "/media/input", "Source": str(root / "input"), "RW": False},
            {"Destination": "/media/output", "Source": str(root / "output"), "RW": True},
        ],
    }
    if change == "label":
        info["Config"]["Labels"]["fiber-bundle.media"] = "foreign"
    elif change == "image":
        info["Config"]["Image"] = "foreign"
    elif change == "mount":
        info["Mounts"][0]["Source"] = "/foreign"
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout=json.dumps([info]).encode()),
    )
    removed = []
    monkeypatch.setattr(worker, "remove", removed.append)
    if change == "owned":
        worker.stop_owned(config, POLICY, identifier)
        assert removed == [identifier]
    else:
        with pytest.raises(InvalidInput):
            worker.stop_owned(config, POLICY, identifier)
        assert removed == []
