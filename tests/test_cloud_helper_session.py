"""The persistent native transport rechecks metadata and releases its subprocess."""

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from test_cloud_directory_status import native as native

from fiber_bundle import cloud_helper_session as transport


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS metadata helper")
def test_native_session_matches_single_calls_and_observes_changes(native, tmp_path):
    helper = native / "helper"
    note = tmp_path / "笔记.md"
    note.write_text("unchanged body")
    placeholder = tmp_path / "pending.icloud"
    placeholder.touch()
    link = tmp_path / "linked"
    link.symlink_to(note)
    process = None
    with transport.reuse():
        session = transport.current.get()
        for mode, path, names in [
            ("--file", note, ()),
            ("--directory", tmp_path, ()),
            ("--directory", note, ()),
            ("--file", placeholder, ()),
            ("--file", link, ()),
            ("--items", tmp_path, (note.name,)),
        ]:
            result = json.loads(transport.query(helper, mode, path, names=names, timeout=5))
            single = subprocess.run(
                [str(helper), mode, str(path)],
                input=json.dumps(names).encode() if names else None,
                capture_output=True,
                check=True,
                timeout=5,
            )
            assert result == json.loads(single.stdout)
            process = process or session.process
            assert session.process is process
        note.unlink()
        assert json.loads(transport.query(helper, "--file", note, timeout=5))["errors"] == 1
        note.mkdir()
        assert json.loads(transport.query(helper, "--directory", note, timeout=5))["available"]
        assert session.process is process
        with transport.reuse():
            assert transport.current.get() is session
    assert process.poll() == 0
    assert transport.current.get() is None
    assert transport.query(helper, "--file", note, timeout=5) is None


@pytest.mark.parametrize("response", ["'x' * 4097", "'{}\\n{}\\n'", "None"])
def test_invalid_or_timed_out_helper_is_closed(tmp_path, response):
    helper = tmp_path / "helper"
    program = (
        "import sys,time\n"
        "sys.stdin.readline()\n"
        f"response = {response}\n"
        "if response is not None: sys.stdout.write(response); sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    helper.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -u -c {shlex.quote(program)}\n"
    )
    helper.chmod(0o700)
    with transport.reuse():
        session = transport.current.get()
        with pytest.raises((ValueError, subprocess.TimeoutExpired)):
            transport.query(helper, "--file", tmp_path, timeout=0.1)
        assert session.process is None


def test_exception_closes_session_and_non_metadata_modes_do_not_start_process(tmp_path):
    with pytest.raises(RuntimeError), transport.reuse():
        session = transport.current.get()
        assert transport.query(Path("missing"), "--tree", tmp_path, timeout=1) is None
        assert session.process is None
        raise RuntimeError("caller failure")
    assert transport.current.get() is None
