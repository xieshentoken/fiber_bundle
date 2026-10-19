import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_media_jobs import media_scope as media_scope

from fiber_bundle import auth, media_jobs, media_link_entry, media_link_worker, media_links_cli
from fiber_bundle import media_link_preflight as preflight
from fiber_bundle import media_link_preflight_capture as capture
from fiber_bundle.config import InvalidInput
from fiber_bundle.media_files import canonical
from fiber_bundle.media_link_failure import AcquisitionBlocked
from fiber_bundle.media_link_metadata import inspect, verify
from fiber_bundle.media_processing_files import write

SPEC = {
    "kind": "metadata_preflight",
    "url": "https://www.youtube.com/watch?v=BaW_jenozKc",
    "mode": "audio_only",
}
INFO = {"id": "BaW_jenozKc", "protocol": "https", "duration": 3.2, "filesize": 100}


@pytest.fixture
def folders(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    write(tmp_path / "input", "request.json", canonical(SPEC))
    return tmp_path


@pytest.mark.parametrize(
    "changes,reason,duration,basis",
    [
        ({}, None, 3200, "reported"),
        ({"duration": 60}, None, 60000, "reported"),
        ({"duration": 60.001}, "duration_over_limit", 60001, "reported"),
        ({"duration": None}, "duration_unverified", None, "reported"),
        ({"duration": True}, "duration_unverified", None, "reported"),
        ({"duration": float("nan")}, "duration_unverified", None, "reported"),
        ({"duration": 10**400}, "duration_unverified", None, "reported"),
        ({"duration": 0.0001}, "duration_unverified", 0, "reported"),
        ({"filesize": None, "filesize_approx": 1024}, None, 3200, "estimated"),
        ({"filesize": None}, "size_unverified", 3200, None),
        ({"filesize": 8 * 1024 * 1024}, None, 3200, "reported"),
        ({"filesize": 8 * 1024 * 1024 + 0.1}, "size_over_limit", 3200, "reported"),
    ],
)
def test_observations_are_bounded_without_claiming_complete_source(
    folders, changes, reason, duration, basis
):
    result = inspect(dict(INFO, **changes), SPEC)
    assert result["reason"] == reason and result["eligible_by_metadata"] == (reason is None)
    assert result["observed_duration_ms"] == duration and result["size_basis"] == basis
    assert all(
        result[k] is False
        for k in [
            "login_verified",
            "source_coverage_verified",
            "publication_ready",
            "cookies_exported",
        ]
    )
    write(folders / "output", "result.json", canonical(result))
    assert verify(folders, SPEC) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "other"),
        ("protocol", "m3u8"),
        ("requested_formats", [{}]),
        ("is_live", True),
        ("has_drm", True),
        ("availability", "needs_auth"),
        ("_type", "playlist"),
    ],
)
def test_preflight_keeps_acquisition_access_and_format_constraints(field, value):
    with pytest.raises(AcquisitionBlocked):
        inspect(dict(INFO, **{field: value}), SPEC)


@pytest.mark.parametrize(
    "field,value",
    [
        ("url", "https://youtu.be/BaW_jenozKc?token=secret"),
        ("mode", "unknown"),
        ("kind", "download"),
        ("cookie", "secret"),
    ],
)
def test_request_does_not_expand_scope(field, value):
    with pytest.raises(InvalidInput):
        inspect(INFO, dict(SPEC, **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("observed_duration_ms", True),
        ("reported_bytes", 0),
        ("eligible_by_metadata", True),
        ("source_coverage_verified", 0),
        ("reason", None),
        ("request_sha256", "0" * 64),
        ("title", "PRIVATE_TITLE"),
    ],
)
def test_host_rejects_changed_fields_and_false_eligibility(folders, field, value):
    result = inspect(dict(INFO, duration=120), SPEC)
    result[field] = value
    write(folders / "output", "result.json", canonical(result))
    with pytest.raises(InvalidInput):
        verify(folders, SPEC)


def test_metadata_extraction_never_downloads_converts_or_leaks_fields(folders):
    calls = []

    class Downloader:
        def __init__(self, options):
            assert options["skip_download"] is True and options["retries"] == 0
            assert options["cookiefile"] is None and options["proxy"] == ""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            assert download is False
            calls.append(url)
            return dict(
                INFO,
                title="PRIVATE_TITLE",
                description="PRIVATE_BODY",
                url="https://cdn.example/secret",
            )

        def process_info(self, info):
            raise AssertionError("must not download")

    with (
        patch.object(capture, "public_sockets"),
        patch.object(
            capture.importlib, "import_module", return_value=SimpleNamespace(YoutubeDL=Downloader)
        ),
        patch.object(subprocess, "run", side_effect=AssertionError("no transcode")),
    ):
        capture.capture(folders / "input", folders / "output")
    result = verify(folders, SPEC)
    assert result["observed_duration_ms"] == 3200 and len(calls) == 1
    assert b"PRIVATE" not in (folders / "output/result.json").read_bytes()
    assert {p.name for p in (folders / "output").iterdir()} == {"result.json"}


def test_entry_dispatch_selects_only_explicit_preflight():
    with (
        patch.object(media_link_entry, "read", return_value=canonical(SPEC)),
        patch.object(capture, "capture") as preview,
        patch.object(media_link_entry, "capture", side_effect=AssertionError("no acquisition")),
    ):
        assert media_link_entry.main() == 0
        preview.assert_called_once()


def test_owner_guard_and_no_task_or_receipt_creation(config, media_scope):
    cid, vid = media_scope[0]["id"], media_scope[1]
    with media_jobs.connection(config) as db:
        before = {
            t: db.execute("SELECT count(*) FROM " + t).fetchone()[0]
            for t in ["media_link_tasks", "media_tasks", "receipts"]
        }

    def execute(config, identity, image, spec, *, guard):
        guard()
        return inspect(INFO, spec)

    with patch.object(media_link_worker, "execute_preflight", side_effect=execute):
        result = preflight.run(config, cid, vid, SPEC["url"], SPEC["mode"], "sha256:" + "a" * 64)
    assert result["state"] == "inspected" and result["publication_ready"] is False
    with media_jobs.connection(config) as db:
        assert before == {t: db.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in before}


def test_revoke_before_result_blocks_metadata_delivery(config, media_scope):
    cid, vid = media_scope[0]["id"], media_scope[1]

    def execute(config, identity, image, spec, *, guard):
        with media_jobs.connection(config) as db:
            auth.set_grant(db, cid, vid, add=False, query=False, original=False)
        return inspect(INFO, spec)

    with patch.object(media_link_worker, "execute_preflight", side_effect=execute):
        with pytest.raises(auth.Denied):
            preflight.run(config, cid, vid, SPEC["url"], SPEC["mode"], "sha256:" + "a" * 64)


def test_failure_is_redacted_and_does_not_trigger_followup(config, media_scope):
    with patch.object(
        media_link_worker, "execute_preflight", side_effect=AcquisitionBlocked("metadata_failed")
    ) as worker:
        result = preflight.run(
            config,
            media_scope[0]["id"],
            media_scope[1],
            SPEC["url"],
            SPEC["mode"],
            "sha256:" + "a" * 64,
        )
    assert result["state"] == "blocked" and result["failure_reason"] == "metadata_failed"
    assert worker.call_count == 1 and result["eligible_by_metadata"] is False


def test_cli_preflight_requires_no_declared_duration_or_link_id(
    config, media_scope, tmp_path, capsys
):
    client, vid = media_scope[0], media_scope[1]
    token = tmp_path / "token"
    token.write_text(client["token"])
    args = [
        "media_links_cli",
        "preflight",
        "--project-root",
        str(config.project_root),
        "--token-file",
        str(token),
        "--vault-id",
        vid,
        "--url",
        SPEC["url"],
        "--image",
        "sha256:" + "a" * 64,
    ]
    with (
        patch("sys.argv", args),
        patch.object(media_links_cli, "load_config", return_value=config),
        patch.object(media_link_worker, "execute_preflight", return_value=inspect(INFO, SPEC)),
    ):
        assert media_links_cli.main() == 0
    assert "observed_duration_ms" in capsys.readouterr().out
