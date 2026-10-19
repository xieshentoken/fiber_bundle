"""Process verification preserves argv boundaries and never exposes environment entries."""

import os
import struct
import subprocess
import sys

import pytest

from fiber_bundle.channels import process_args
from fiber_bundle.config import InvalidInput


def test_darwin_decoder_retains_spaces_empty_arguments_and_excludes_environment():
    expected = ["/Applications/Fiber bundle/python", "-m", "gateway", "", "a path/file.json"]
    raw = (
        struct.pack("=i", len(expected))
        + b"/Applications/Fiber bundle/python\0\0\0"
        + b"\0".join(os.fsencode(arg) for arg in expected)
        + b"\0SYNTHETIC_ENV_SECRET=not-an-argument\0"
    )
    assert process_args.darwin_arguments(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        struct.pack("=i", 0) + b"python\0",
        struct.pack("=i", 129) + b"python\0",
        struct.pack("=i", 2) + b"python\0\0python\0missing-terminator",
    ],
)
def test_darwin_decoder_rejects_invalid_or_truncated_arguments(raw):
    with pytest.raises(ValueError):
        process_args.darwin_arguments(raw)


@pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"),
    reason="kernel argv inspection supports macOS and Linux",
)
def test_owned_process_arguments_keep_path_spaces_and_exclude_environment(tmp_path):
    settings = str(tmp_path / "Fiber bundle" / "test receiver.json")
    script = "import time; print('ready', flush=True); time.sleep(20)"
    command = [sys.executable, "-c", script, "--settings", settings]
    child = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "FIBER_SYNTHETIC_ENV_SECRET": "not-an-argument"},
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        assert process_args.arguments(child.pid) == command
    finally:
        child.terminate()
        child.wait(timeout=5)
        if child.stdout:
            child.stdout.close()


@pytest.mark.parametrize("pid", [0, 1, -1, True, "2"])
def test_invalid_pid_is_rejected(pid):
    with pytest.raises(InvalidInput, match="channel_legacy_process_unverified"):
        process_args.arguments(pid)
