import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from fiber_bundle import media_capabilities


def test_missing_tools_do_not_make_routes_ready_or_execute_original_skill(monkeypatch):
    monkeypatch.setattr(media_capabilities.shutil, "which", lambda name: None)
    result = media_capabilities.inspect()
    assert all(v["state"] == "MISSING" for v in result["tools"].values())
    assert all(v["state"] == "NOT_RUN" for v in result["routes"].values())
    assert not result["ready_for_pipeline"] and result["model_calls"] == 0
    assert result["source_reference"]["state"] == "NOT_RUN"


@pytest.mark.parametrize("valid", [True, False])
def test_real_version_process_has_no_secret_environment_or_home_and_redacts_output(
    tmp_path, monkeypatch, valid
):
    root = tmp_path.resolve()
    executable = root / "ffmpeg"
    output = "ffmpeg version 1.2.3" if valid else "private-secret-output"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ -n "$TEST_MEDIA_SECRET" ]; then echo private-secret-output; exit 9; fi\n'
        'if [ -e "$HOME/private-config" ]; then echo private-secret-output; exit 8; fi\n'
        f"echo '{output}'\n"
        "echo private-stderr >&2\n"
    )
    executable.chmod(0o700)
    (root / "private-config").touch()
    monkeypatch.setenv("TEST_MEDIA_SECRET", "private-secret")
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(
        media_capabilities.shutil,
        "which",
        lambda name: str(executable) if name == "ffmpeg" else None,
    )
    result = media_capabilities.inspect()
    assert result["tools"]["ffmpeg"]["state"] == ("PASS" if valid else "BLOCKED")
    if valid:
        assert result["tools"]["ffmpeg"]["version"] == "1.2.3"
        assert (
            result["tools"]["ffmpeg"]["executable_sha256"]
            == hashlib.sha256(executable.read_bytes()).hexdigest()
        )
    assert "private" not in json.dumps(result)
    assert not result["ready_for_pipeline"]


def test_timeout_blocks_only_tool_without_retry(monkeypatch):
    attempts = []
    monkeypatch.setattr(
        media_capabilities.shutil,
        "which",
        lambda name: "/synthetic/ffmpeg" if name == "ffmpeg" else None,
    )

    def timeout(args, **kwargs):
        attempts.append(args)
        raise subprocess.TimeoutExpired(args, 5)

    monkeypatch.setattr(media_capabilities.subprocess, "run", timeout)
    result = media_capabilities.inspect()
    assert len(attempts) == 1 and result["tools"]["ffmpeg"]["state"] == "BLOCKED"
    assert result["tools"]["ffprobe"]["state"] == "MISSING"


def test_source_reference_size_hash_and_symlink_drift_fail_closed(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / "SKILL.md").write_text("synthetic skill reference")
    content = (root / "SKILL.md").read_bytes()
    manifest = {
        "source_files_sha256": {"SKILL.md": hashlib.sha256(content).hexdigest()},
        "source_files_bytes": {"SKILL.md": len(content)},
        "license": "not_declared",
        "skill_frontmatter_name": "synthetic",
    }
    read_text = Path.read_text

    def read(path, *args, **kwargs):
        if path.name == "bili-note-source.json":
            return json.dumps(manifest)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert media_capabilities.source_status(root)["state"] == "PASS"
    (root / "SKILL.md").write_bytes(b"changed")
    assert media_capabilities.source_status(root)["state"] == "BLOCKED"
    (root / "SKILL.md").write_bytes(b"x" * len(content))
    assert media_capabilities.source_status(root)["state"] == "BLOCKED"
    (root / "SKILL.md").unlink()
    (root / "SKILL.md").symlink_to(root / "missing")
    assert media_capabilities.source_status(root)["state"] == "BLOCKED"


def test_pinned_reference_copies_no_upstream_code_and_records_name_mismatch():
    manifest = json.loads(
        (Path(media_capabilities.__file__).parent / "vendor/bili-note-source.json").read_text()
    )
    assert manifest["mode"] == "reference_only" and manifest["code_copied"] is False
    assert manifest["skill_display_name"] == "Bili Note V3"
    assert manifest["skill_frontmatter_name"] == "bili-note-v2"
    assert set(manifest["source_files_sha256"]) == set(manifest["source_files_bytes"])
