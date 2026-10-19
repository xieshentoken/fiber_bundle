import os

import pytest

from fiber_bundle import media_capture, media_files, media_subtitles
from fiber_bundle.config import InvalidInput
from fiber_bundle.media_candidate import candidate, metadata, request

RAW = b"1\r\n00:00:00,000 --> 00:00:03,000\r\nLiteral synthetic caption.\r\n"
SOURCE = b"synthetic matrix test; not a parsed media fixture"
SPEC = {
    "format": "wav",
    "evidence_mode": "audio_only",
    "declared_duration_ms": 3000,
    "subtitle_kind": "srt",
}
PROBE = {
    "duration_ms": 3000,
    "streams": [{"kind": "audio", "codec": "pcm_s16le", "sample_rate": 8000, "channels": 1}],
}


def files(root, raw=RAW, spec=SPEC, probe=PROBE):
    root.mkdir()
    value = candidate(SOURCE, raw, spec, probe)
    media_files.write(root, "result.json", media_files.canonical(value))
    if raw:
        media_files.write(
            root, "normalized.srt", media_subtitles.normalized(value["subtitle"]["cues"])
        )
    return value


def test_literal_raw_and_normalized_bindings(tmp_path):
    output = tmp_path / "output"
    value = files(output)
    assert media_capture.verify(output, SOURCE, RAW, SPEC) == value
    assert value["subtitle"]["cues"] == [
        {"start_ms": 0, "end_ms": 3000, "text": "Literal synthetic caption."}
    ]
    assert value["state"] == "candidate" and value["source_bytes_verified"]
    assert value["subtitle"]["raw_sha256"] != value["subtitle"]["normalized_sha256"]
    assert value["subtitle"]["corrections"] == []
    assert not any(value[k] for k in ("publication_ready", "cleanup_allowed", "knowledge_verified"))


@pytest.mark.parametrize(
    "raw,kind",
    [
        (b"WEBVTT\n\n00:00.000 --> 00:03.000\nLiteral synthetic caption.\n", "vtt"),
        (b"WEBVTT\n\ncue-1\n00:00:00.000 --> 00:00:03.000\nLiteral synthetic caption.\n", "vtt"),
        (RAW, "srt"),
    ],
)
def test_explicit_subtitle_subset(raw, kind):
    assert media_subtitles.parse(raw, kind, 3000)[0]["text"] == "Literal synthetic caption."


@pytest.mark.parametrize(
    "raw",
    [
        RAW.replace(b"03,000", b"04,000"),
        RAW.replace(b"00,000", b"60,000"),
        RAW.replace(b"03,000", b"00,000"),
        RAW.replace(b"1\r", b"2\r"),
        RAW.replace(b"caption.", b"<b>caption.</b>"),
        RAW.replace(b"caption.", b"caption.\x00"),
        b"\xff",
        b"",
        b"x" * (65536 + 1),
    ],
)
def test_bad_timestamp_encoding_or_text_rejected(raw):
    with pytest.raises(InvalidInput):
        media_subtitles.parse(raw, "srt", 3000)


@pytest.mark.parametrize(
    "raw",
    [
        b"WEBVTT\n\nNOTE private\ncontent\n",
        b"WEBVTT\n\n00:00.000 --> 00:03.000 align:start\nx\n",
        b"WEBVTT private header\n\n00:00.000 --> 00:03.000\nx\n",
    ],
)
def test_unsupported_vtt_features_fail_closed(raw):
    with pytest.raises(InvalidInput):
        media_subtitles.parse(raw, "vtt", 3000)


def test_missing_preview_and_visual_are_not_complete_evidence(tmp_path):
    missing = candidate(SOURCE, None, {**SPEC, "subtitle_kind": None}, PROBE)
    assert missing["state"] == "pending" and missing["reasons"] == ["subtitle_missing"]
    preview = candidate(SOURCE, RAW, {**SPEC, "declared_duration_ms": 4000}, PROBE)
    assert preview["state"] == "blocked" and "source_duration_mismatch" in preview["reasons"]
    video = candidate(
        SOURCE,
        RAW,
        {**SPEC, "format": "mp4", "evidence_mode": "full_evidence"},
        {
            "duration_ms": 3000,
            "streams": [{"kind": "video", "codec": "mpeg4", "width": 160, "height": 90}],
        },
    )
    assert video["state"] == "pending" and video["reasons"] == ["visual_not_reviewed"]
    assert video["request"]["evidence_mode"] == "full_evidence"


def test_gaps_and_out_of_order_ranges():
    raw = b"1\n00:00:00,500 --> 00:00:01,000\nfirst\n\n2\n00:00:02,000 --> 00:00:02,500\nlast\n"
    value = candidate(SOURCE, raw, SPEC, PROBE)
    assert value["subtitle"]["uncovered_ranges"] == [
        {"start_ms": 0, "end_ms": 500},
        {"start_ms": 1000, "end_ms": 2000},
        {"start_ms": 2500, "end_ms": 3000},
    ]
    with pytest.raises(InvalidInput):
        media_subtitles.parse(raw.replace(b"02,000", b"00,000"), "srt", 3000)


@pytest.mark.parametrize("change", ["source", "subtitle", "text", "normalized", "extra"])
def test_host_rejects_changed_binding_or_output_scope(tmp_path, change):
    output = tmp_path / "output"
    value = files(output)
    if change == "text":
        value["subtitle"]["cues"][0]["text"] = "invented"
        (output / "result.json").write_bytes(media_files.canonical(value))
    elif change == "normalized":
        (output / "normalized.srt").write_bytes(b"changed")
    elif change == "extra":
        (output / "foreign").write_bytes(b"private")
    with pytest.raises(InvalidInput):
        media_capture.verify(
            output,
            SOURCE + b"x" if change == "source" else SOURCE,
            RAW + b"x" if change == "subtitle" else RAW,
            SPEC,
        )


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "oversize"])
def test_unsafe_files_do_not_read_private_target(tmp_path, kind):
    target = tmp_path / "private"
    target.write_bytes(b"private sentinel")
    root = tmp_path / "root"
    root.mkdir()
    path = root / "source"
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"x" * 20)
    with pytest.raises((InvalidInput, OSError)):
        media_files.read(root, "source", 10)
    assert target.read_bytes() == b"private sentinel"


@pytest.mark.parametrize(
    "field", ["format", "evidence_mode", "declared_duration_ms", "subtitle_kind"]
)
def test_request_rejects_wrong_types(field):
    with pytest.raises(InvalidInput):
        request({**SPEC, field: []})


@pytest.mark.parametrize("mutate", ["codec", "rate", "channels", "duration", "extra"])
def test_format_matrix_rejects_unsupported_or_unknown_probe(mutate):
    stream = dict(PROBE["streams"][0])
    probe = {**PROBE, "streams": [stream]}
    if mutate == "codec":
        stream["codec"] = "mp3"
    elif mutate == "rate":
        stream["sample_rate"] = 96000
    elif mutate == "channels":
        stream["channels"] = 6
    elif mutate == "duration":
        probe["duration_ms"] = True
    else:
        probe["private_tag"] = "private"
    with pytest.raises(InvalidInput):
        metadata(probe, "wav")
