import hashlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_media_jobs import fixture_worker
from test_media_jobs import media_scope as media_scope

from fiber_bundle import auth, media_jobs, media_link_capture, media_link_worker, media_links
from fiber_bundle.config import InvalidInput
from fiber_bundle.media_files import canonical, decode
from fiber_bundle.media_link_artifact import duration
from fiber_bundle.media_link_failure import AcquisitionBlocked
from fiber_bundle.media_processing_files import write

SPEC = {
    "url": "https://www.youtube.com/watch?v=BaW_jenozKc",
    "mode": "audio_only",
    "declared_duration_ms": 3000,
}


def report(data, *, measured=3001, version=2):
    value = {
        "schema": f"fiber-media-link-capture/v{version}",
        **SPEC,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_bytes": len(data),
        "media_type": "audio/wav",
        "access_state": "public_acquired",
        "source_coverage_verified": False,
        "cookies_exported": False,
    }
    if version == 2:
        value["converted_duration_ms"] = measured
    return value


@pytest.mark.parametrize("version,expected", [(1, 3000), (2, 3001)])
def test_versioned_output_preserves_platform_duration_and_binds_bytes(tmp_path, version, expected):
    data = b"synthetic-file"
    value = report(data, version=version)
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    write(tmp_path / "input", "request.json", canonical(SPEC))
    write(tmp_path / "output", "source", data)
    write(tmp_path / "output", "result.json", canonical(value))
    verified, content = media_link_worker.verify(tmp_path, SPEC)
    assert duration(verified, content, SPEC) == expected
    assert verified["declared_duration_ms"] == 3000
    assert verified["source_coverage_verified"] is False
    with pytest.raises(InvalidInput):
        duration(verified, b"changed", SPEC)


@pytest.mark.parametrize("measured", [None, False, True, 0, -1, 60001, 3001.0, "3001", []])
def test_converted_duration_is_strictly_bounded(measured):
    with pytest.raises(InvalidInput):
        duration(report(b"file", measured=measured), b"file", SPEC)


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema", "other"),
        ("declared_duration_ms", 2999),
        ("source_bytes", 99),
        ("cookies_exported", True),
        ("source_coverage_verified", True),
        ("extra", "unbound"),
        ("access_state", "browser_copy_attested"),
    ],
)
def test_unbound_or_promoted_claim_is_rejected(key, value):
    with pytest.raises(InvalidInput):
        duration(dict(report(b"file"), **{key: value}), b"file", SPEC)


def test_legacy_report_cannot_add_a_new_duration_without_versioning():
    with pytest.raises(InvalidInput):
        duration(dict(report(b"file", version=1), converted_duration_ms=3001), b"file", SPEC)


@pytest.mark.parametrize("seconds,expected", [("30.046500", 30047), ("60.000000", 60000)])
def test_conversion_measurement_uses_exact_file_time_and_half_up_rounding(
    tmp_path, seconds, expected
):
    output = canonical({"format": {"duration": seconds}})
    with patch.object(
        media_link_capture.subprocess, "run", return_value=SimpleNamespace(stdout=output)
    ) as command:
        assert media_link_capture.converted_duration(tmp_path / "source.wav", True) == expected
    args = command.call_args.args[0]
    assert args[0] == "ffprobe" and args[args.index("-protocol_whitelist") + 1] == "file"
    assert "-ss" not in args and "-t" not in args
    assert command.call_args.kwargs["timeout"] == 20


@pytest.mark.parametrize("seconds", ["NaN", "Infinity", "0", "60.000001", "bad", "0.00001"])
def test_invalid_or_overlong_conversion_is_rejected(seconds, tmp_path):
    with patch.object(
        media_link_capture.subprocess,
        "run",
        return_value=SimpleNamespace(stdout=canonical({"format": {"duration": seconds}})),
    ):
        with pytest.raises(AcquisitionBlocked) as blocked:
            media_link_capture.converted_duration(tmp_path / "source", True)
    assert blocked.value.reason == "output_invalid"


def test_link_job_uses_measured_time_but_keeps_platform_evidence(config, media_scope):
    cid, vid = media_scope[0]["id"], media_scope[1]
    data = media_scope[2][0][2]
    identity = media_links.create(
        config, cid, vid, SPEC["url"], SPEC["mode"], 3000, "sha256:" + "a" * 64
    )
    with patch.object(media_link_worker, "execute", return_value=(report(data), data)):
        result = media_links.run(config, cid, identity, "sha256:" + "b" * 64)
    with media_jobs.connection(config) as db:
        link = media_links.access(config, db, cid, identity)
        assert link["declared_duration_ms"] == 3000
        bound = decode(link["binding_json"].encode())
        assert bound["capture"]["converted_duration_ms"] == 3001
        job = media_jobs.load(db, cid, result["job_id"])
        assert media_jobs.binding(job)["request"]["declared_duration_ms"] == 3001
    # The independently probed 3000 ms fixture still rejects an inaccurate 3001 ms claim.
    with patch("fiber_bundle.media_worker.execute", side_effect=fixture_worker):
        media_jobs.run(config, cid, result["job_id"])
    with media_jobs.connection(config) as db:
        auth.set_grant(db, cid, vid, add=True, query=True, original=True)
    candidate = media_jobs.result(config, cid, result["job_id"])
    assert candidate["state"] == "blocked" and "source_duration_mismatch" in candidate["reasons"]
    assert candidate["publication_ready"] is False
