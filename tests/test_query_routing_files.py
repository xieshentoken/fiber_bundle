import io
import json
import os
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest
from test_query_routing_preparation import routing as routing

from fiber_bundle.config import InvalidInput
from fiber_bundle.query_routing_config import load, parse
from fiber_bundle.query_routing_credentials import load as load_credentials
from fiber_bundle.query_routing_files import read
from fiber_bundle.query_routing_preview import preview
from fiber_bundle.query_routing_probe import _main


def after_read(action):
    """Perform a real filesystem mutation after the descriptor's second stat."""
    real_stat = os.fstat
    calls = 0

    def changing_stat(fd):
        nonlocal calls
        result = real_stat(fd)
        calls += 1
        if calls == 2:
            action()
        return result

    return patch("fiber_bundle.query_routing_files.os.fstat", side_effect=changing_stat)


def replace_file(path):
    info = path.stat()
    path.rename(path.with_suffix(".old"))
    path.write_bytes(b"x" * info.st_size)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))


def test_parent_symlink_swap_after_path_validation_never_opens_leaf(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    path = parent / "input.json"
    path.write_bytes(b"safe")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / path.name).write_bytes(b"PRIVATE OFF-PATH CONTENT")
    real_open = os.open
    leaves = []

    def swapping_open(name, flags, *args, **kwargs):
        if name == parent.name:
            parent.rename(tmp_path / "old-parent")
            parent.symlink_to(outside, target_is_directory=True)
        if name == path.name:
            leaves.append(name)
        return real_open(name, flags, *args, **kwargs)

    with patch("fiber_bundle.query_routing_files.os.open", side_effect=swapping_open):
        with pytest.raises(OSError):
            read(path, 100)
    assert leaves == []


def test_leaf_replacement_with_equal_size_and_mtime_is_rejected(tmp_path):
    path = tmp_path / "input.json"
    path.write_bytes(b"original")
    with after_read(lambda: replace_file(path)):
        with pytest.raises(InvalidInput, match="routing_file_changed"):
            read(path, 100)


@pytest.mark.parametrize("replacement", ["directory", "symlink", "missing"])
def test_parent_replacement_during_read_is_rejected(tmp_path, replacement):
    parent = tmp_path / "parent"
    parent.mkdir()
    path = parent / "input.json"
    path.write_bytes(b"original")

    def mutate():
        parent.rename(tmp_path / "old-parent")
        if replacement == "directory":
            parent.mkdir()
            path.write_bytes(b"original")
        elif replacement == "symlink":
            parent.symlink_to(tmp_path / "old-parent", target_is_directory=True)

    with after_read(mutate):
        with pytest.raises(InvalidInput, match="routing_file_changed"):
            read(path, 100)


def test_in_place_edit_with_restored_size_and_mtime_is_rejected(tmp_path):
    path = tmp_path / "input.json"
    path.write_bytes(b"original")
    real_stat = os.fstat
    calls = 0

    def mutate(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            info = path.stat()
            path.write_bytes(b"modified")
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        return real_stat(fd)

    with patch("fiber_bundle.query_routing_files.os.fstat", side_effect=mutate):
        with pytest.raises(InvalidInput, match="routing_file_changed"):
            read(path, 100)


def test_unrelated_sibling_creation_does_not_reject_snapshot(tmp_path):
    path = tmp_path / "input.json"
    path.write_bytes(b"original")
    with after_read(lambda: (tmp_path / "sibling").write_bytes(b"unrelated")):
        assert read(path, 100) == b"original"


@pytest.mark.parametrize("failure", ["none", "initial", "recheck", "changed"])
def test_all_descriptors_are_closed_on_success_and_failure(tmp_path, failure):
    parent = tmp_path / "parent"
    parent.mkdir()
    path = parent / "input.json"
    path.write_bytes(b"original")
    real_open = os.open
    opened = []
    leaf_opens = 0

    def tracking_open(name, flags, *args, **kwargs):
        nonlocal leaf_opens
        if name == path.name:
            leaf_opens += 1
            if (failure == "initial" and leaf_opens == 1) or (
                failure == "recheck" and leaf_opens == 2
            ):
                raise OSError("synthetic open failure")
        fd = real_open(name, flags, *args, **kwargs)
        opened.append(fd)
        return fd

    with patch("fiber_bundle.query_routing_files.os.open", side_effect=tracking_open):
        if failure == "none":
            assert read(path, 100) == b"original"
        elif failure == "changed":
            with after_read(lambda: replace_file(path)):
                with pytest.raises(InvalidInput):
                    read(path, 100)
        else:
            with pytest.raises((OSError, InvalidInput)):
                read(path, 100)
    assert opened
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("caller", ["policy", "answer", "credential"])
def test_routing_callers_reject_replaced_snapshots(routing, tmp_path, caller):
    path = tmp_path / "input.json"
    value = routing if caller == "policy" else {"schema": "local-answer/v1", "answer": "safe"}
    path.write_text(json.dumps(value) if caller != "credential" else "synthetic-token")
    with after_read(lambda: replace_file(path)):
        if caller == "credential":
            assert load_credentials(Path(path)) == (None, None, "credential_file_rejected")
        else:
            with pytest.raises(InvalidInput, match="routing_file_changed"):
                load(path) if caller == "policy" else preview(parse(routing), path)


def test_credential_replacement_stops_worker_before_http(routing, tmp_path):
    secret = tmp_path / "private-key.txt"
    secret.write_text("synthetic-private-token")
    routing["local_query"]["secret_file"] = str(secret)
    payload = json.dumps({"local": asdict(parse(routing).local_query), "read_credentials": True})
    output = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(payload)),
        patch("sys.stdout", output),
        patch("fiber_bundle.query_routing_probe._request", side_effect=AssertionError("HTTP")),
        after_read(lambda: replace_file(secret)),
    ):
        assert _main() == 0
    assert json.loads(output.getvalue()) == {
        "reason": "credential_file_rejected",
        "credentials_read": None,
    }
    assert (
        str(secret) not in output.getvalue() and "synthetic-private-token" not in output.getvalue()
    )
