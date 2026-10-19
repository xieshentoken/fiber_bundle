import copy
import hashlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_media_jobs import media_scope as media_scope

from fiber_bundle import media_link_capture, media_link_entry, media_link_worker, media_links
from fiber_bundle.config import InvalidInput
from fiber_bundle.media_files import canonical
from fiber_bundle.media_link_failure import (
    REASONS,
    AcquisitionBlocked,
    from_code,
    reason,
    record,
    reported,
    stored,
)
from fiber_bundle.media_processing_files import write

SPEC = {
    "url": "https://www.youtube.com/watch?v=BaW_jenozKc",
    "mode": "audio_only",
    "declared_duration_ms": 3000,
}


@pytest.fixture
def folders(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    write(tmp_path / "input", "request.json", canonical(SPEC))
    return tmp_path


@pytest.mark.parametrize("code", sorted(REASONS))
def test_fixed_categories_round_trip_without_remote_text(folders, code):
    record(folders / "input", folders / "output", AcquisitionBlocked(code))
    assert reported(folders, SPEC) == code
    assert from_code(stored(AcquisitionBlocked(code))) == code
    body = (folders / "output/result.json").read_bytes()
    assert SPEC["url"].encode() not in body
    assert (folders / "output/result.json").stat().st_mode & 0o777 == 0o600


def test_arbitrary_error_and_mutated_reason_never_become_diagnostic(folders):
    private = "PRIVATE_URL_TOKEN_OWNER_AND_PROVIDER_BODY"
    error = AcquisitionBlocked("metadata_failed")
    error.reason = private
    assert reason(error) == "unclassified"
    record(folders / "input", folders / "output", RuntimeError(private))
    assert reported(folders, SPEC) == "unclassified"
    assert private.encode() not in (folders / "output/result.json").read_bytes()
    assert from_code("media_link_attempt_blocked") == "unclassified"
    assert from_code("media_link_attempt_blocked:" + private) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "remote-secret"),
        ("reason", []),
        ("schema", "wrong"),
        ("request_sha256", "0" * 64),
        ("raw_error", "PRIVATE_BODY"),
    ],
)
def test_untrusted_failure_report_is_strictly_bound(folders, field, value):
    report = {
        "schema": "fiber-media-link-failure/v1",
        "request_sha256": hashlib.sha256(canonical(SPEC)).hexdigest(),
        "reason": "metadata_failed",
    }
    report[field] = value
    write(folders / "output", "result.json", canonical(report))
    with pytest.raises(InvalidInput):
        reported(folders, SPEC)


@pytest.mark.parametrize(
    "kind", ["changed_request", "extra_file", "symlink", "hardlink", "oversized", "noncanonical"]
)
def test_invalid_report_files_are_not_adopted(folders, kind):
    record(folders / "input", folders / "output", AcquisitionBlocked("metadata_failed"))
    path = folders / "output/result.json"
    if kind == "changed_request":
        (folders / "input/request.json").write_bytes(
            canonical(dict(SPEC, declared_duration_ms=2000))
        )
    elif kind == "extra_file":
        (folders / "output/source").write_bytes(b"partial")
    elif kind == "symlink":
        original = path.read_bytes()
        path.unlink()
        target = folders / "elsewhere"
        target.write_bytes(original)
        path.symlink_to(target)
    elif kind == "hardlink":
        import os

        os.link(path, folders / "linked")
    elif kind == "oversized":
        path.write_bytes(b" " * 1025)
    else:
        path.write_bytes(b' {"reason":"metadata_failed"}')
    with pytest.raises((OSError, InvalidInput)):
        reported(folders, SPEC)


@pytest.mark.parametrize("name", ["source", "result.json", "other"])
def test_diagnostic_does_not_replace_partial_or_success_effects(folders, name):
    path = folders / "output" / name
    path.write_bytes(b"preserved")
    before = path.stat()
    record(folders / "input", folders / "output", AcquisitionBlocked("output_invalid"))
    assert path.read_bytes() == b"preserved" and path.stat().st_ino == before.st_ino
    assert {p.name for p in path.parent.iterdir()} == {name}


@pytest.mark.parametrize(
    "case,expected",
    [
        ("metadata", "metadata_failed"),
        ("live", "source_unsupported"),
        ("playlist", "source_unsupported"),
        ("restricted", "access_restricted"),
        ("protocol", "format_unsupported"),
        ("formats", "format_unsupported"),
        ("id", "source_id_mismatch"),
        ("duration", "duration_mismatch"),
        ("size", "size_unverified"),
        ("download", "download_failed"),
        ("progress", "download_size_exceeded"),
    ],
)
def test_capture_reports_stage_and_never_reextracts_or_retries(folders, case, expected):
    info = {"id": "BaW_jenozKc", "duration": 3.0, "filesize": 100, "protocol": "https"}
    changes = {
        "live": {"is_live": True},
        "playlist": {"_type": "playlist"},
        "restricted": {"availability": "needs_auth"},
        "protocol": {"protocol": "m3u8"},
        "formats": {"requested_formats": [{}]},
        "id": {"id": "different"},
        "duration": {"duration": 2.5},
        "size": {"filesize": None},
    }
    info.update(changes.get(case, {}))
    calls = []

    class Downloader:
        def __init__(self, options):
            self.options = options
            assert options["cookiefile"] is None and options["retries"] == 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            calls.append("metadata")
            assert download is False
            if case == "metadata":
                raise RuntimeError("PRIVATE_PROVIDER_BODY")
            return copy.deepcopy(info)

        def process_info(self, value):
            calls.append("download")
            if case == "progress":
                self.options["progress_hooks"][0]({"downloaded_bytes": 8 * 1024 * 1024 + 1})
            raise RuntimeError("PRIVATE_DOWNLOAD_BODY")

    with (
        patch.object(media_link_capture, "public_sockets"),
        patch.object(
            media_link_capture.importlib,
            "import_module",
            return_value=SimpleNamespace(YoutubeDL=Downloader),
        ),
    ):
        with pytest.raises(AcquisitionBlocked) as failure:
            media_link_capture.capture(folders / "input", folders / "output")
    assert failure.value.reason == expected
    assert calls.count("metadata") == 1 and calls.count("download") <= 1
    record(folders / "input", folders / "output", failure.value)
    assert b"PRIVATE_" not in (folders / "output/result.json").read_bytes()


def test_entry_reports_safe_stderr_even_when_recording_fails(capsys):
    with (
        patch.object(media_link_entry, "capture", side_effect=RuntimeError("PRIVATE_BODY")),
        patch.object(media_link_entry, "record", side_effect=OSError("PRIVATE_PATH")),
    ):
        assert media_link_entry.main() == 1
    text = capsys.readouterr().err
    assert "diagnostic_unavailable" in text and "access_blocked" in text
    assert "PRIVATE" not in text


def test_host_nonzero_exit_adopts_only_bound_diagnostic(config):
    from uuid import uuid4

    identity = str(uuid4())
    root = media_link_worker.directory(config, identity)

    def start(*args, **kwargs):
        record(root / "input", root / "output", AcquisitionBlocked("duration_mismatch"))
        return SimpleNamespace(poll=lambda: 1, returncode=1)

    with (
        patch.object(media_link_worker.subprocess, "run"),
        patch.object(media_link_worker.subprocess, "Popen", side_effect=start),
        patch.object(media_link_worker, "stop_owned") as cleanup,
        patch.object(
            media_link_worker, "verify", side_effect=AssertionError("failed output must not import")
        ),
    ):
        with pytest.raises(AcquisitionBlocked) as failure:
            media_link_worker.execute(
                config, identity, "sha256:" + "a" * 64, SPEC, guard=lambda: None
            )
    assert failure.value.reason == "duration_mismatch" and cleanup.call_count == 1


def test_persistent_reason_preserves_public_error_code_and_blocks_replay(config, media_scope):
    cid, vid = media_scope[0]["id"], media_scope[1]
    identity = media_links.create(
        config, cid, vid, SPEC["url"], "audio_only", 3000, "sha256:" + "a" * 64
    )
    with patch.object(
        media_link_worker, "execute", side_effect=AcquisitionBlocked("duration_mismatch")
    ) as attempt:
        with pytest.raises(InvalidInput):
            media_links.run(config, cid, identity, "sha256:" + "b" * 64)
        report = media_links.status(config, cid, identity)
        assert report["state"] == "blocked" and report["error_code"] == "media_link_attempt_blocked"
        assert report["failure_reason"] == "duration_mismatch" and report["login_verified"] is False
        with pytest.raises(InvalidInput, match="no_replay"):
            media_links.run(config, cid, identity, "sha256:" + "b" * 64)
        assert attempt.call_count == 1
