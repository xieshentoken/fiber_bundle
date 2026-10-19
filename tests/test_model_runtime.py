"""Trust qualification and precise runtime failures, without changing installed images."""

import json
from types import SimpleNamespace

import pytest

from fiber_bundle.model_configuration import runtime
from fiber_bundle.model_configuration.errors import ModelError


def reply(info, code=0):
    return SimpleNamespace(returncode=code, stdout=json.dumps([info]).encode())


def info():
    manifest = runtime.manifest()
    return {
        "Id": manifest["image"],
        "Architecture": manifest["architecture"],
        "Config": {"Labels": {"local.fiber-bundle.model-recipe": manifest["recipe_sha256"]}},
    }


def test_exact_trusted_image_qualifies(monkeypatch):
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: reply(info()))
    assert runtime.inspect()["state"] == "ready"


@pytest.mark.parametrize("field,value", [("Architecture", "amd64"), ("Id", "sha256:" + "0" * 64)])
def test_wrong_architecture_or_digest_fails(monkeypatch, field, value):
    observed = info()
    observed[field] = value
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: reply(observed))
    with pytest.raises(ModelError, match="runtime_changed"):
        runtime.inspect()


def test_daemon_failure_is_not_reported_as_missing_image(monkeypatch):
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: reply({}, 1))
    with pytest.raises(ModelError, match="docker_unavailable"):
        runtime.inspect()


def test_bad_archive_cannot_load(config, store, tmp_path, monkeypatch):
    archive = tmp_path / "wrong.tar.gz"
    archive.write_bytes(b"synthetic invalid archive")
    monkeypatch.setattr(runtime, "inspect", lambda *a: {"state": "missing"})
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: calls.append(a))
    runtime.prepare(config, archive)
    assert runtime.preparation(config)["error_code"] == "model_runtime_changed"
    assert calls == []


def test_runtime_read_never_prepares_or_starts_jobs(http, monkeypatch):
    calls = []

    def check(command, **kwargs):
        calls.append(command)
        observed = info()
        observed["Id"] = command[-1]
        return reply(observed)

    monkeypatch.setattr(runtime.subprocess, "run", check)
    response = http.get("/admin/models/runtime")
    assert response.status_code == 200
    assert {command[-1] for command in calls} == {
        runtime.expected_image(),
        runtime.expected_image("creation"),
    }
    assert all(command[:3] == ["docker", "image", "inspect"] for command in calls)
