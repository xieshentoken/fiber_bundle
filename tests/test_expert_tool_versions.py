"""Resume uses the original source bytes, including proposal and container entry code."""

import importlib.util
import json
import marshal
import struct
from pathlib import Path

import pytest

from fiber_bundle import expert_tool_versions as versions
from fiber_bundle.config import InvalidInput
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest


def test_capture_and_tamper_fail_closed(config, monkeypatch):
    identity = versions.capture(config)
    frozen = config.state_root / "expert-tool-snapshots" / identity
    assert digest((frozen / "files.json").read_bytes()) == identity
    original = versions._files
    here = Path(versions.__file__).parent

    def changed(root):
        files = original(root)
        return (
            {**files, "expert_proposals.py": b"# newer implementation"} if root == here else files
        )

    monkeypatch.setattr(versions, "_files", changed)
    assert versions.verify(config, identity) == frozen
    source = frozen / "expert_proposals.py"
    cached = Path(importlib.util.cache_from_source(str(source)))
    cached.parent.mkdir()
    info = source.stat()
    cached.write_bytes(
        importlib.util.MAGIC_NUMBER
        + struct.pack("<III", 0, int(info.st_mtime), info.st_size)
        + marshal.dumps(
            compile("raise AssertionError('unverified pyc executed')", str(source), "exec")
        )
    )
    tools, units = versions.load(config, identity)
    assert Path(tools.__file__).parent == frozen
    assert Path(units.validate.__code__.co_filename).parent == frozen
    assert tools.code_digest() == identity
    (frozen / "expert_proposals.py").write_bytes(b"tampered")
    with pytest.raises(InvalidInput, match="tools_changed"):
        versions.verify(config, identity)


def test_unknown_version_never_falls_back(config):
    with pytest.raises(InvalidInput, match="tools_changed"):
        versions.load(config, "f" * 64)


def test_shipped_legacy_bytes_match_manifest(config):
    root = Path(versions.__file__).parent / "expert_tool_snapshots"
    for folder in root.iterdir():
        files = versions._files(folder)
        manifest = canonical({name: digest(raw) for name, raw in files.items()}).encode()
        assert digest(manifest) == folder.name
        assert json.loads((folder / "files.json").read_bytes()) == json.loads(manifest)
