import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import fiber_bundle
from fiber_bundle.config import InvalidInput
from fiber_bundle.media_contract import MAX_MANIFEST_BYTES, MAX_MEDIA_BYTES, validate
from fiber_bundle.media_preflight import inspect
from fiber_bundle.media_preflight_cli import read_manifest

FIXTURE = Path(__file__).parent / "fixtures/m9/preflight.json"


def job():
    return json.loads(FIXTURE.read_text())


def raw(value):
    return json.dumps(value).encode()


def assert_no_effects(value):
    for key in [
        "source_bytes_verified",
        "evidence_files_verified",
        "visual_review_verified",
        "glossary_confirmation_verified",
        "publication_ready",
        "knowledge_verified",
        "cleanup_allowed",
    ]:
        assert value[key] is False
    assert value["authorization"] in {"NOT_VERIFIED", "DENIED"}


def test_complete_metadata_is_advisory_and_original_is_kept():
    source = FIXTURE.read_bytes()
    view = inspect(source)
    assert view["state"] == "inspected" and view["metadata_checks_passed"]
    assert view["manifest_sha256"] == hashlib.sha256(source).hexdigest()
    assert view["local_duration_ms"] == view["transcript_covered_ms"] == 3000
    assert view["visual_covered_ms"] is None
    assert view["audio_only_disclosure"] == "音频优先整理，未核验画面内容"
    assert view["source_retention"] == "keep_original"
    assert_no_effects(view)


@pytest.mark.parametrize("declared", [None, 10_000, 1000])
def test_missing_declaration_or_preview_never_changes_coverage_denominator(declared):
    value = job()
    value["source"]["declared_duration_ms"] = declared
    view = inspect(raw(value))
    assert view["state"] == "blocked" and not view["metadata_checks_passed"]
    assert view["declared_duration_ms"] == declared
    assert any("duration" in r for r in view["blocking_reasons"])
    assert_no_effects(view)


@pytest.mark.parametrize("state", ["partial", "preview_limited", "unverified"])
def test_source_state_not_replaced_with_local_duration_success(state):
    value = job()
    value["source"]["source_state"] = state
    assert "source_coverage_unverified" in inspect(raw(value))["blocking_reasons"]


def test_interval_union_does_not_double_count_overlap_or_hide_gaps():
    value = job()
    value["transcript"]["cues"][1].update(start_ms=0, end_ms=1500)
    view = inspect(raw(value))
    assert view["transcript_covered_ms"] == 1500
    assert "transcript_coverage_incomplete" in view["blocking_reasons"]
    value["transcript"]["cues"][1].update(start_ms=1000, end_ms=3000)
    assert inspect(raw(value))["transcript_covered_ms"] == 3000


def test_missing_and_failed_transcript_remain_blocked():
    value = job()
    value["transcript"].update(kind="none", raw_sha256=None, normalized_sha256=None, cues=[])
    assert "transcript_coverage_incomplete" in inspect(raw(value))["blocking_reasons"]
    value = job()
    value["transcript"]["failed_ranges"] = [{"start_ms": 0, "end_ms": 1500}]
    assert "transcript_failures_present" in inspect(raw(value))["blocking_reasons"]


@pytest.mark.parametrize("route", ["subtitle", "asr"])
@pytest.mark.parametrize("confirmed", [None, "2" * 64, "1" * 64])
def test_current_glossary_sha_claim_does_not_certify_human_confirmation(confirmed, route):
    value = job()
    value["transcript"]["kind"] = route
    value["glossary"].update(sha256="1" * 64, confirmed_sha256=confirmed)
    view = inspect(raw(value))
    assert ("glossary_confirmation_missing_or_stale" in view["blocking_reasons"]) == (
        confirmed != "1" * 64
    )
    assert_no_effects(view)


def test_unresolved_terms_block_even_when_confirmation_sha_matches():
    value = job()
    value["glossary"].update(sha256="1" * 64, confirmed_sha256="1" * 64, unresolved_count=1)
    assert "terminology_unresolved" in inspect(raw(value))["blocking_reasons"]


@pytest.mark.parametrize(
    "permission,cancelled", [("denied", False), ("unverified", True), ("denied", True)]
)
def test_revocation_or_cancel_cannot_be_overridden_by_complete_metadata(permission, cancelled):
    view = inspect(FIXTURE.read_bytes(), permission=permission, cancelled=cancelled)
    assert not view["metadata_checks_passed"]
    assert ("current_authorization_denied" in view["blocking_reasons"]) == (permission == "denied")
    assert ("cancelled" in view["blocking_reasons"]) == cancelled
    assert_no_effects(view)


@pytest.mark.parametrize(
    "kwargs",
    [{"permission": "authorized"}, {"permission": {}}, {"permission": True}, {"cancelled": 1}],
)
def test_scope_inputs_cannot_forge_grants(kwargs):
    with pytest.raises(InvalidInput):
        inspect(FIXTURE.read_bytes(), **kwargs)


@pytest.mark.parametrize(
    "state,count,covered,unresolved,passed",
    [
        ("missing", 0, 0, 0, False),
        ("partial", 2, 3000, 0, False),
        ("reviewed", 2, 1500, 0, False),
        ("reviewed", 2, 3000, 1, False),
        ("reviewed", 2, 3000, 0, True),
    ],
)
def test_full_video_requires_separate_visual_coverage_claims(
    state, count, covered, unresolved, passed
):
    value = job()
    value["evidence_mode"] = "full_evidence"
    value["source"]["media_kind"] = "video"
    value["visual"].update(
        state=state,
        frame_count=count,
        unresolved_count=unresolved,
        coverage_ranges=[{"start_ms": 0, "end_ms": covered}] if covered else [],
    )
    view = inspect(raw(value))
    assert view["metadata_checks_passed"] is passed
    assert view["audio_only_disclosure"] is None
    assert_no_effects(view)


@pytest.mark.parametrize(
    "keep,ownership,retention",
    [
        (False, "job_owned", "keep_until_both_audits_pass"),
        (True, "job_owned", "keep_requested"),
        (False, "user_original", "keep_original"),
    ],
)
def test_link_ownership_is_explicit_and_cleanup_never_runs(keep, ownership, retention):
    value = job()
    value["source"].update(origin="link", ownership=ownership, keep_media=keep)
    view = inspect(raw(value))
    assert view["source_retention"] == retention
    assert_no_effects(view)


@pytest.mark.parametrize(
    "section,key,invalid",
    [
        (None, "schema", "legacy-v3-job"),
        (None, "evidence_mode", "automatic"),
        ("source", "byte_count", True),
        ("source", "byte_count", MAX_MEDIA_BYTES + 1),
        ("source", "byte_count", 0),
        ("source", "local_duration_ms", 0),
        ("source", "local_duration_ms", float("nan")),
        ("source", "declared_duration_ms", True),
        ("source", "sha256", "private-api-key"),
        ("source", "keep_media", 1),
        ("source", "origin", "youtube-playlist"),
        ("source", "ownership", "job_owned"),
        ("source", "filename", "../../private-file"),
        ("source", "signed_url", "private-token"),
        ("transcript", "raw_sha256", None),
        ("transcript", "kind", "guessed"),
        ("transcript", "cues", [{"start_ms": 0, "end_ms": 3001, "text_sha256": "1" * 64}]),
        ("transcript", "cues", [{"start_ms": True, "end_ms": 3, "text_sha256": "1" * 64}]),
        ("transcript", "cues", [{"start_ms": 10, "end_ms": 3, "text_sha256": "1" * 64}]),
        ("transcript", "cues", [{"start_ms": 0, "end_ms": 3000, "text": "private-body"}]),
        ("transcript", "cues", [None]),
        ("glossary", "unresolved_count", -1),
        ("visual", "frame_count", 1),
        ("visual", "state", "reviewed"),
        ("visual", "coverage_ranges", [{"start_ms": 0, "end_ms": 3000}]),
    ],
)
def test_bad_fields_numbers_paths_and_audio_visual_claims_are_rejected(section, key, invalid):
    value = job()
    target = value if section is None else value[section]
    target[key] = invalid
    with pytest.raises(InvalidInput) as caught:
        inspect(raw(value))
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "source",
    [
        b"",
        b"x" * (MAX_MANIFEST_BYTES + 1),
        b"\xff",
        b"{",
        b'{"secret":1,"secret":2}',
        b'{"value":Infinity}',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_unsafe_json_is_rejected(source):
    with pytest.raises(InvalidInput):
        inspect(source)


def test_range_order_and_limit_are_not_silently_repaired():
    value = job()
    value["transcript"]["cues"].reverse()
    with pytest.raises(InvalidInput):
        validate(value)
    value = job()
    value["transcript"]["cues"] = [copy.deepcopy(value["transcript"]["cues"][0])] * 101
    with pytest.raises(InvalidInput):
        validate(value)


def test_cli_reads_one_manifest_without_other_files_or_writes_and_cannot_grant(tmp_path):
    root = tmp_path.resolve()
    sample = root / "private-path-manifest.json"
    sample.write_bytes(FIXTURE.read_bytes())
    (root / "metadata.sqlite3").write_bytes(b"untouched")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(fiber_bundle.__file__).resolve().parents[1]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    command = [
        sys.executable,
        "-m",
        "fiber_bundle.media_preflight_cli",
        "inspect",
        "--manifest",
        str(sample),
    ]
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and not result.stderr and "private-path" not in result.stdout
    assert_no_effects(json.loads(result.stdout))
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
    result = subprocess.run(
        command + ["--permission", "denied", "--cancelled"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert json.loads(result.stdout)["state"] == "blocked"
    sample.write_bytes(b'{"private-api-key":')
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1 and not result.stderr and "private" not in result.stdout


@pytest.mark.parametrize("kind", ["directory", "symlink", "fifo", "oversize", "empty"])
def test_manifest_io_rejects_unsafe_files(tmp_path, kind):
    path = tmp_path.resolve() / "manifest"
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to(path.parent / "missing")
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"x" * (MAX_MANIFEST_BYTES + 1) if kind == "oversize" else b"")
    with pytest.raises((OSError, InvalidInput)):
        read_manifest(path)
