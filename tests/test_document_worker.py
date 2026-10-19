import subprocess
import sys
from uuid import uuid4

import pytest
from document_support import candidate

from fiber_bundle import document_worker as worker
from fiber_bundle.document_contract import DocumentError
from fiber_bundle.inputs import canonical

POLICY = worker.DocumentPolicy("sha256:" + "a" * 64, 1)
SOURCE = b"%PDF-synthetic-worker-contract"


@pytest.mark.parametrize("image", ["latest", "python:3.13", "sha256:abc"])
def test_image_requires_immutable_digest(image):
    with pytest.raises(DocumentError):
        worker.DocumentPolicy(image).validate()


def test_container_limits_and_private_mounts(tmp_path):
    inputs, outputs = tmp_path / "input", tmp_path / "output"
    inputs.mkdir()
    outputs.mkdir()
    args = worker.command(POLICY, inputs, outputs, "fiber-document-" + str(uuid4()))
    assert args.count("--mount") == 2
    assert f"type=bind,source={inputs},target=/document/input,readonly" in args
    assert f"type=bind,source={outputs},target=/document/output" in args
    for key, value in [
        ("--user", "1000:1000"),
        ("--network", "none"),
        ("--memory", "256m"),
        ("--cap-drop", "ALL"),
        ("--log-driver", "none"),
    ]:
        assert args[args.index(key) + 1] == value
    assert "--read-only" in args and "no-new-privileges" in args
    assert not {"--env", "--publish", "--privileged", "/var/run/docker.sock"} & set(args)


def test_denied_before_files(config):
    def denied():
        raise PermissionError("synthetic denial")

    with pytest.raises(PermissionError):
        worker.execute_document(config, POLICY, SOURCE, guard=denied)
    assert not config.state_root.exists()


def setup_child(monkeypatch, config, *, invalid=False, sleeping=False):
    created, removed, launched = [], [], []
    real_popen = subprocess.Popen
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda *args, **kwargs: created.append(args[0]) or subprocess.CompletedProcess(args[0], 0),
    )
    monkeypatch.setattr(worker, "remove", removed.append)

    def start(args, **kwargs):
        launched.append(args)
        name = args[-1].removeprefix("fiber-document-")
        output = config.state_root / "document-captures" / name / "output" / "result.json"
        raw = b"{}" if invalid else canonical(candidate(SOURCE)).encode()
        code = "import sys,time;from pathlib import Path;sys.stdin.buffer.read();"
        code += "time.sleep(5);" if sleeping else ""
        code += f"Path({str(output)!r}).write_bytes({raw!r})"
        return real_popen([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(worker.subprocess, "Popen", start)
    return created, removed, launched


def test_real_child_result_source_binding_and_no_replay(config, monkeypatch):
    created, removed, launched = setup_child(monkeypatch, config)
    result = worker.execute_document(config, POLICY, SOURCE, guard=lambda: None)
    assert len(created) == len(removed) == len(launched) == 1
    assert (result.directory / "input/source.pdf").read_bytes() == SOURCE
    assert result.result["pages"][0]["text"].startswith("Synthetic original")
    before = (result.directory / "output/result.json").read_bytes()
    with pytest.raises(DocumentError, match="document_attempt_exists"):
        worker.execute_document(config, POLICY, SOURCE, guard=lambda: None, task_id=result.task_id)
    assert (result.directory / "output/result.json").read_bytes() == before
    assert len(created) == 1


def test_revoked_after_create_cleans_without_launch(config, monkeypatch):
    created, removed, launched = setup_child(monkeypatch, config)

    def guard():
        if created:
            raise PermissionError("revoked")

    with pytest.raises(PermissionError):
        worker.execute_document(config, POLICY, SOURCE, guard=guard)
    assert len(created) == len(removed) == 1 and not launched


def test_revoked_at_delivery_never_exposes_candidate(config, monkeypatch):
    created, removed, launched = setup_child(monkeypatch, config)

    def guard():
        roots = config.state_root / "document-captures"
        if roots.exists() and any(roots.glob("*/output/result.json")):
            raise PermissionError("revoked before delivery")

    with pytest.raises(PermissionError):
        worker.execute_document(config, POLICY, SOURCE, guard=guard)
    assert len(created) == len(removed) == len(launched) == 1
    assert list((config.state_root / "document-captures").glob("*/input/source.pdf"))


@pytest.mark.parametrize("kind", ["invalid", "timeout"])
def test_failure_preserves_original_and_stops_writer(config, monkeypatch, kind):
    created, removed, launched = setup_child(
        monkeypatch, config, invalid=kind == "invalid", sleeping=kind == "timeout"
    )
    with pytest.raises(DocumentError):
        worker.execute_document(config, POLICY, SOURCE, guard=lambda: None)
    assert len(created) == len(removed) == len(launched) == 1
    original = next((config.state_root / "document-captures").glob("*/input/source.pdf"))
    assert original.read_bytes() == SOURCE
    assert (
        not (original.parent.parent / "output/result.json").exists() if kind == "timeout" else True
    )


def test_create_failure_never_removes_existing_foreign_container(config, monkeypatch):
    removed = []

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "docker create")

    monkeypatch.setattr(worker.subprocess, "run", fail)
    monkeypatch.setattr(worker, "remove", removed.append)
    with pytest.raises(subprocess.CalledProcessError):
        worker.execute_document(config, POLICY, SOURCE, guard=lambda: None)
    assert removed == []
    assert (
        next((config.state_root / "document-captures").glob("*/input/source.pdf")).read_bytes()
        == SOURCE
    )


@pytest.mark.parametrize("kind", ["label", "image", "mount", "owned"])
def test_orphan_cleanup_only_removes_matching_owned_worker(config, monkeypatch, kind):
    import json

    identifier = str(uuid4())
    root = config.state_root / "document-captures" / identifier
    info = {
        "Config": {"Labels": {"fiber-bundle.document": identifier}, "Image": POLICY.image},
        "Mounts": [
            {"Destination": "/document/input", "Source": str(root / "input"), "RW": False},
            {"Destination": "/document/output", "Source": str(root / "output"), "RW": True},
        ],
    }
    if kind == "label":
        info["Config"]["Labels"]["fiber-bundle.document"] = "foreign"
    elif kind == "image":
        info["Config"]["Image"] = "foreign"
    elif kind == "mount":
        info["Mounts"][0]["Source"] = "/foreign"
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=json.dumps([info]).encode()
        ),
    )
    removed = []
    monkeypatch.setattr(worker, "remove", removed.append)
    if kind == "owned":
        worker.stop_owned(config, POLICY, identifier)
        assert removed == ["fiber-document-" + identifier]
    else:
        with pytest.raises(DocumentError, match="document_cleanup_scope"):
            worker.stop_owned(config, POLICY, identifier)
        assert removed == []
