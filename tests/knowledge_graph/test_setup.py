"""Runtime discovery does not activate models; metadata pagination respects current permissions."""

import subprocess

import pytest
from fastapi.testclient import TestClient

from fiber_bundle import auth, database
from fiber_bundle.api import create_app
from fiber_bundle.config import InvalidInput
from fiber_bundle.knowledge_graph import note_listing, runtime_setup

from .test_lifecycle import small as small


def test_local_only_probe_rejects_remote_context(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"tcp://untrusted:2375\n", b"")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(InvalidInput, match="graph_local_engine_required"):
        runtime_setup.detect()
    assert len(calls) == 1


def test_runtime_not_running_returns_safe_diagnostic(monkeypatch):
    def run(*args, **kwargs):
        raise FileNotFoundError("docker absent")

    monkeypatch.setattr(subprocess, "run", run)
    result = runtime_setup.detect()
    assert result["state"] == "unavailable" and result["policy_saved"] is False
    assert result["model_calls"] == 0


def test_current_notes_search_offset_and_read_permissions(small):
    cfg, cid, vid, *_ = small
    with database.connect(cfg.state_root) as db:
        notes = note_listing.listing(cfg, db, cid, vid)
        assert len(notes) == 3 and all(n["selectable"] for n in notes)
        assert len(note_listing.listing(cfg, db, cid, vid, "海星")) == 1
        assert note_listing.listing(cfg, db, cid, vid, offset=2) == notes[2:]
        auth.set_grant(db, cid, vid, query=False, original=False, add=True)
        with pytest.raises(auth.Denied):
            note_listing.listing(cfg, db, cid, vid)


def test_detection_requires_admin_csrf_and_does_not_save_policy(small, monkeypatch):
    cfg, *_ = small
    before = (cfg.state_root / "knowledge-graph-policy.json").read_bytes()
    calls = []

    def probe():
        import fcntl
        import os

        fd = os.open(cfg.state_root / "storage.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        calls.append("probe")
        return {"state": "unavailable"}

    monkeypatch.setattr(runtime_setup, "detect", probe)
    client = TestClient(
        create_app(cfg), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 20000)
    )
    path = "/admin/knowledge-graph/runtime/detect"
    assert client.post(path).status_code == 403 and not calls
    client.headers["Origin"] = "http://127.0.0.1:8765"
    assert client.post(path).status_code == 401 and not calls
    auth.set_password(cfg, "synthetic-graph-setup-password")
    client.headers["Origin"] = "http://127.0.0.1:8765"
    login = client.post("/admin/login", json={"password": "synthetic-graph-setup-password"})
    assert client.post(path).status_code == 403 and not calls
    client.headers["X-CSRF-Token"] = login.json()["csrf"]
    assert client.post(path).json()["state"] == "unavailable" and calls == ["probe"]
    assert (cfg.state_root / "knowledge-graph-policy.json").read_bytes() == before
