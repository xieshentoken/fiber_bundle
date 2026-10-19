"""Unknown builds stay unknown; isolated provenance excludes private project material."""

import importlib.util
import json
from pathlib import Path

import pytest

from fiber_bundle import build_identity, setup_cli
from fiber_bundle.config import InvalidInput

SCRIPT = Path(__file__).parents[1] / "desktop/build_candidate.py"
spec = importlib.util.spec_from_file_location("build_candidate", SCRIPT)
assert spec is not None and spec.loader is not None
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_health_and_setup_report_recorded_identity_or_explicit_unknown(http):
    identity = build_identity.inspect()
    assert identity["state"] in {"recorded", "unidentified"}
    if identity["state"] == "unidentified":
        assert identity["build_id"] is None
    assert http.get("/health").json() == {"status": "ok", "build": build_identity.inspect()}
    assert setup_cli.inspect()["build"] == build_identity.inspect()


def test_recorded_identity_contains_no_paths_and_is_not_a_verified_integrity_claim(
    tmp_path, monkeypatch
):
    identity = build_identity.candidate("0.5.0rc1", {"src/fiber_bundle/one.py": "a" * 64})
    monkeypatch.setattr(build_identity, "__file__", str(tmp_path / "build_identity.py"))
    path = tmp_path / "build_identity.json"
    assert build_identity.inspect() == {"state": "unidentified", "build_id": None}
    path.write_text(json.dumps(identity))
    assert build_identity.inspect() == {"state": "recorded", **identity}
    assert "/" not in json.dumps(build_identity.inspect()).replace("candidate-identity/v1", "")
    identity["build_id"] = "invented"
    path.write_text(json.dumps(identity))
    with pytest.raises(InvalidInput, match="build_identity_invalid"):
        build_identity.inspect()
    path.write_bytes(b"x" * 4097)
    with pytest.raises(InvalidInput):
        build_identity.inspect()


@pytest.fixture
def public_source(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    for name in builder.ROOT_FILES:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("synthetic public file")
    for name in [
        "src/fiber_bundle/one.py",
        "tests/one.py",
        "desktop/main.mjs",
        "configs/task.example.json",
        "src/fiber_bundle/document_executor/Dockerfile.ocr",
    ]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic public source")
    return root


def test_candidate_snapshot_excludes_private_roots_and_records_copied_bytes(
    public_source, tmp_path
):
    root = public_source
    for name in [
        ".local-state/metadata.sqlite3",
        "test vault/private.md",
        ".env",
        "config.test.json",
        "configs/actual-credential.json",
        "docs/evidence/private.json",
    ]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private-sentinel")
    copied = tmp_path / "snapshot"
    files = builder.snapshot(root, copied)
    assert "docs/TECHNICAL_REFERENCE.md" in files
    assert "docs/HANDOFF.md" in files
    assert "configs/task.example.json" in files
    assert "src/fiber_bundle/document_executor/Dockerfile.ocr" in files
    assert all("private-sentinel" not in p.read_text() for p in copied.rglob("*") if p.is_file())
    assert all(builder.digest(copied / name) == sha for name, sha in files.items())
    old = build_identity.candidate("0.5.0rc1", files)
    (root / "src/fiber_bundle/one.py").write_text("a different implementation")
    new = build_identity.candidate("0.5.0rc1", builder.snapshot(root, tmp_path / "snapshot2"))
    assert old["version"] == new["version"]
    assert old["build_id"] != new["build_id"] and old["runtime_sha256"] != new["runtime_sha256"]


def test_candidate_refuses_linked_source_and_existing_outputs(public_source, tmp_path):
    path = public_source / "src/fiber_bundle/linked.py"
    path.symlink_to(public_source / "README.md")
    with pytest.raises(InvalidInput, match="candidate_source_symlink"):
        builder.snapshot(public_source, tmp_path / "snapshot")
    with pytest.raises(InvalidInput, match="candidate_fresh_output_required"):
        builder.build(public_source, public_source)


def test_candidate_keeps_complete_project_skills_without_runtime_cache(public_source, tmp_path):
    for name in ("skills/bili-note-V3/assets/template", "skills/fake-expert/SKILL.md"):
        path = public_source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public skill asset")
    cache = public_source / "skills/bili-note-V3/__pycache__/private.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"cache sentinel")
    files = builder.snapshot(public_source, tmp_path / "snapshot")
    assert "skills/bili-note-V3/assets/template" in files
    assert "skills/fake-expert/SKILL.md" in files
    assert not any("__pycache__" in n for n in files)


def test_candidate_refuses_linked_public_tree_before_traversing(
    public_source, tmp_path, monkeypatch
):
    private = tmp_path / "private-root"
    private.mkdir()
    (private / "secret.py").write_text("private-sentinel")
    tree = public_source / "src/fiber_bundle"
    tree.rename(tmp_path / "original-public")
    tree.symlink_to(private, target_is_directory=True)
    original = Path.rglob

    def glob(path, pattern):
        if path == tree:
            pytest.fail("linked private tree traversed before rejection")
        return original(path, pattern)

    monkeypatch.setattr(Path, "rglob", glob)
    with pytest.raises(InvalidInput, match="symlink"):
        builder.snapshot(public_source, tmp_path / "snapshot")


def test_source_change_during_snapshot_does_not_create_provenance(
    public_source, tmp_path, monkeypatch
):
    original = builder.shutil.copyfile

    def copying(source, target):
        result = original(source, target)
        if source.name == "one.py":
            source.write_text("changed while copying")
        return result

    monkeypatch.setattr(builder.shutil, "copyfile", copying)
    with pytest.raises(InvalidInput, match="candidate_source_changed"):
        builder.snapshot(public_source, tmp_path / "snapshot")
    assert not (tmp_path / "snapshot/src/fiber_bundle/build_identity.json").exists()


def test_candidate_keeps_offline_fonts_preload_and_graph_build_inputs(public_source, tmp_path):
    expected = {
        "src/fiber_bundle/web/dm-sans.ttf",
        "desktop/preload.cjs",
        "frontend/graph/renderer.js",
        "frontend/graph/build.mjs",
    }
    for name in expected:
        path = public_source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic public asset")
    values = builder.snapshot(public_source, tmp_path / "snapshot")
    assert expected <= values.keys()
    for name in expected:
        assert (tmp_path / "snapshot" / name).read_text() == "synthetic public asset"
