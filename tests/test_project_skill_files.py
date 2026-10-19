"""Directory changes select new immutable Skill versions without touching older tasks."""

import json
from pathlib import Path

import pytest

from fiber_bundle import project_skill_files as files
from fiber_bundle import project_skills
from fiber_bundle.config import InvalidInput


@pytest.fixture
def project_skill(config):
    root = config.project_root / "skills/fake-expert"
    values = {
        "SKILL.md": (
            b"---\nname: fake-expert\ndescription: Technical PDF compiler\n---\nVersion A\n"
        ),
        "requirements-compiler.txt": b"pypdf==6.10.0\n",
        "scripts/fake_expert.py": b"# offline compiler fixture\n",
        "scripts/compiler_version.py": (
            b'RELEASE_VERSION = "1.2.0"\nUX_CLI_PROTOCOL = "fake-expert-ux-cli-v0.2"\n'
        ),
        "assets/schemas/semantic-draft.schema.json": b"{}",
        "references/reading.md": b"source-bound material A",
    }
    for name, raw in values.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    return root


def test_directory_update_and_removal_retain_exact_old_snapshot(config, project_skill):
    old = files.capture(config)
    old_root = files.snapshot_path(config, old["snapshot_sha256"])
    (project_skill / "references/reading.md").write_bytes(b"new references B")
    new = files.capture(config)
    assert new["snapshot_sha256"] != old["snapshot_sha256"]
    assert new["compiler_sha256"] != old["compiler_sha256"]
    assert new["pi_sha256"] == old["pi_sha256"]
    assert (old_root / "skill/references/reading.md").read_bytes() == b"source-bound material A"
    (project_skill / "SKILL.md").unlink()
    assert files.verify(config, old["snapshot_sha256"]) == old
    with pytest.raises(InvalidInput):
        files.capture(config)
    assert project_skills.inspect(config)["state"] == "invalid"


@pytest.mark.parametrize("area", ["SKILL.md", "references/reading.md", "pi.md", "skill-files.json"])
def test_changed_snapshot_fails_closed(config, project_skill, area):
    snapshot = files.capture(config)
    root = files.snapshot_path(config, snapshot["snapshot_sha256"])
    path = root / (area if area in {"pi.md", "skill-files.json"} else "skill/" + area)
    path.chmod(0o600)
    path.write_bytes(b"tampered")
    with pytest.raises(InvalidInput):
        files.verify(config, snapshot["snapshot_sha256"])


def test_symlink_and_unsupported_protocol_rejected(config, project_skill, tmp_path):
    (project_skill / "references/escape").symlink_to(tmp_path)
    with pytest.raises(InvalidInput, match="path_invalid"):
        files.capture(config)
    (project_skill / "references/escape").unlink()
    (project_skill / "scripts/compiler_version.py").write_text('RELEASE_VERSION = "other"')
    with pytest.raises(InvalidInput, match="protocol_incompatible"):
        files.capture(config)


def test_inspection_is_read_only_and_does_not_enable(config, project_skill):
    status = project_skills.inspect(config)
    assert status["state"] == "detected"
    assert status["settings"]["enabled"] is False
    assert status["snapshot"]["files"] == 6
    assert not (config.state_root / "project-skills").exists()
    assert not (config.state_root / "configs/project-skills.json").exists()
    assert json.loads(json.dumps(status))["snapshot"]["qualified"] is False


def test_version_capabilities_are_explicit_and_future_runtime_is_rejected(config, project_skill):
    version = project_skill / "scripts/compiler_version.py"
    version.write_text(
        version.read_text()
        + '\nDIRECT_REFERENCE_COMPILER_VERSION = "1.4.0-direct-reference"\n'
        + 'REFERENCE_SEARCH_RUNTIME_VERSION = "reference-search-bm25-v0.3"\n'
    )
    runtime = project_skill / "scripts/reference_search.py"
    raw = (Path(files.__file__).parent / "vendor/expert_reference_search.py").read_bytes()
    runtime.write_bytes(raw)
    snapshot = files.capture(config)
    assert snapshot["features"]["figures"] == "index+images"
    runtime.write_bytes(raw + b"\n# same protocol, different runtime\n")
    with pytest.raises(InvalidInput, match="protocol_incompatible"):
        files.capture(config)
    assert files.verify(config, snapshot["snapshot_sha256"]) == snapshot
    runtime.write_bytes(raw)
    version.write_text(version.read_text().replace("reference-search-bm25-v0.3", "future-v1"))
    with pytest.raises(InvalidInput, match="protocol_incompatible"):
        files.capture(config)
    assert files.verify(config, snapshot["snapshot_sha256"]) == snapshot


def test_encrypted_backup_restores_source_and_frozen_versions(
    config, store, project_skill, tmp_path
):
    from fiber_bundle import backup_bundle, backup_restore
    from fiber_bundle.config import load_config

    snapshot = files.capture(config)
    from fiber_bundle import expert_tool_versions

    tools = expert_tool_versions.capture(config)
    target = tmp_path / "skill-backup"
    backup_bundle.create(config, target, "synthetic-project-skill-password")
    destination = tmp_path / "restored-skill"
    backup_restore.restore(target, destination, "synthetic-project-skill-password")
    restored = load_config(destination / "config.test.json", destination)
    assert (restored.project_root / "skills/fake-expert/SKILL.md").read_bytes() == (
        project_skill / "SKILL.md"
    ).read_bytes()
    assert files.verify(restored, snapshot["snapshot_sha256"])["files"] == snapshot["files"]
    for name in (*expert_tool_versions.FILES, "files.json"):
        original = config.state_root / "expert-tool-snapshots" / tools / name
        copied = restored.state_root / "expert-tool-snapshots" / tools / name
        assert copied.read_bytes() == original.read_bytes()
