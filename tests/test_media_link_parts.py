import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fiber_bundle import media_link_capture, media_link_worker
from fiber_bundle.media_files import canonical
from fiber_bundle.media_link_failure import AcquisitionBlocked
from fiber_bundle.media_link_metadata import checked_info, inspect
from fiber_bundle.media_link_parts import leaf, parts, reported_size

URL = "https://www.bilibili.com/video/BV1XC4y197GR"
SPEC = {"url": URL, "mode": "full_evidence", "declared_duration_ms": 3000}
VIDEO = {
    "url": "https://example.com/video",
    "ext": "mp4",
    "protocol": "https",
    "format_id": "v",
    "height": 720,
    "width": 1280,
    "vcodec": "hev1",
    "acodec": "none",
    "filesize": 200,
}
AUDIO = {
    "url": "https://example.com/audio",
    "ext": "m4a",
    "protocol": "https",
    "format_id": "a",
    "vcodec": "none",
    "acodec": "mp4a",
    "filesize": 100,
}
INFO = {
    "id": "BV1XC4y197GR",
    "title": "synthetic split source",
    "duration": 3.0,
    "protocol": "https+https",
    "requested_formats": [VIDEO, AUDIO],
}


def test_explicit_full_mode_checks_pair_and_aggregates_each_part():
    before = copy.deepcopy(INFO)
    checked = checked_info(INFO, URL, mode="full_evidence")
    assert parts(checked, "full_evidence") == [VIDEO, AUDIO]
    spec = {"kind": "metadata_preflight", "url": URL, "mode": "full_evidence"}
    result = inspect(INFO, spec)
    assert result["reported_bytes"] == 300 and result["size_basis"] == "reported"
    assert result["eligible_by_metadata"] and not result["source_coverage_verified"]
    assert INFO == before
    with pytest.raises(AcquisitionBlocked):
        checked_info(INFO, URL)


@pytest.mark.parametrize(
    "change",
    [
        "reversed",
        "third",
        "missing",
        "nested",
        "manifest",
        "drm",
        "live",
        "http",
        "credentials",
        "port",
        "height",
        "width",
        "video_audio",
        "audio_video",
        "same_url",
        "ext",
        "codec",
        "protocol",
        "leaf_type",
    ],
)
def test_invalid_split_selections_never_reach_download(change):
    value = copy.deepcopy(INFO)
    video, audio = value["requested_formats"]
    if change == "reversed":
        value["requested_formats"].reverse()
    elif change == "third":
        value["requested_formats"].append(copy.deepcopy(AUDIO))
    elif change == "missing":
        value["requested_formats"] = [video]
    elif change == "nested":
        video["requested_formats"] = [VIDEO]
    elif change == "manifest":
        video["fragments"] = [{"url": "https://example.com/x"}]
    elif change == "drm":
        video["has_drm"] = True
    elif change == "live":
        audio["is_live"] = True
    elif change == "http":
        audio["url"] = "http://example.com/a"
    elif change == "credentials":
        video["url"] = "https://user:private@example.com/v"
    elif change == "port":
        video["url"] = "https://example.com:444/v"
    elif change == "height":
        video["height"] = 721
    elif change == "width":
        video["width"] = True
    elif change == "video_audio":
        video["acodec"] = "aac"
    elif change == "audio_video":
        audio["vcodec"] = "h264"
    elif change == "same_url":
        audio["url"] = video["url"]
    elif change == "ext":
        audio["ext"] = "mp4"
    elif change == "codec":
        video["vcodec"] = None
    elif change == "protocol":
        value["protocol"] = "m3u8"
    elif change == "leaf_type":
        value["requested_formats"] = [video, 42]
    with pytest.raises(AcquisitionBlocked) as failure:
        checked_info(value, URL, mode="full_evidence")
    assert failure.value.reason == "format_unsupported"


@pytest.mark.parametrize("bad", [None, True, 0, -1, float("inf"), 10**400])
def test_unknown_part_size_cannot_be_hidden_by_combined_size(bad):
    value = copy.deepcopy(INFO)
    value["filesize"] = 1
    value["requested_formats"][1]["filesize"] = bad
    assert reported_size(value, "full_evidence") == (None, None)
    result = inspect(value, {"kind": "metadata_preflight", "url": URL, "mode": "full_evidence"})
    assert not result["eligible_by_metadata"] and result["reason"] == "size_unverified"


def test_mixed_estimates_and_aggregate_limit_are_conservative():
    value = copy.deepcopy(INFO)
    audio = value["requested_formats"][1]
    audio["filesize"] = None
    audio["filesize_approx"] = 100.1
    assert reported_size(value, "full_evidence") == (301, "estimated")
    value["requested_formats"][0]["filesize"] = 8 * 1024 * 1024
    result = inspect(value, {"kind": "metadata_preflight", "url": URL, "mode": "full_evidence"})
    assert result["reason"] == "size_over_limit" and not result["eligible_by_metadata"]


def test_leaf_has_no_sections_or_automatic_merge_or_postprocess_route():
    value = dict(
        INFO, section_start=1, section_end=2, __postprocessors=["private"], requested_downloads=[{}]
    )
    selected = leaf(value, VIDEO)
    assert selected["id"] == INFO["id"] and selected["url"] == VIDEO["url"]
    assert set(selected).isdisjoint(
        {
            "requested_formats",
            "requested_downloads",
            "section_start",
            "section_end",
            "__postprocessors",
        }
    )
    options = media_link_capture.download_options("full_evidence")
    assert options["fixup"] == "never" and options["postprocessors"] == []
    assert options["retries"] == 0 and options["cookiefile"] is None


@pytest.mark.parametrize(
    "failure", [None, "second_download", "aggregate_progress", "aggregate_file"]
)
def test_capture_downloads_exact_checked_parts_once_and_only_converts_local_files(
    tmp_path, failure
):
    inputs = tmp_path / "input"
    outputs = tmp_path / "output"
    work = tmp_path / "downloads"
    for directory in [inputs, outputs, work]:
        directory.mkdir()
    (inputs / "request.json").write_bytes(canonical(SPEC))
    calls = []

    class Downloader:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            assert url == URL and download is False
            calls.append("metadata")
            return copy.deepcopy(INFO)

        def prepare_filename(self, value):
            return str(work / ("download." + value["ext"]))

        def process_info(self, value):
            assert "requested_formats" not in value and value["protocol"] == "https"
            calls.append(value["format_id"])
            if value["format_id"] == "a" and failure == "second_download":
                raise RuntimeError("PRIVATE_REMOTE_ERROR")
            if value["format_id"] == "v":
                size = (
                    5 * 1024 * 1024 if failure in ("aggregate_progress", "aggregate_file") else 200
                )
            else:
                size = (
                    4 * 1024 * 1024 if failure in ("aggregate_progress", "aggregate_file") else 100
                )
            for hook in self.options["progress_hooks"]:
                hook({"downloaded_bytes": size if failure != "aggregate_file" else 0})
            Path(self.prepare_filename(value)).write_bytes(b"x" * size)

    def convert(command, **kwargs):
        calls.append("conversion")
        assert command[0] == "ffmpeg" and command.count("-i") == 2
        assert (
            command.count("-protocol_whitelist") == 2 and "0:v:0" in command and "1:a:0" in command
        )
        assert not any(str(x).startswith("https:") for x in command)
        Path(command[-1]).write_bytes(b"converted-file")

    def path(value):
        return work if str(value) == "/tmp" else Path(value)

    with (
        patch.object(media_link_capture, "Path", side_effect=path),
        patch.object(media_link_capture, "public_sockets"),
        patch.object(
            media_link_capture.importlib,
            "import_module",
            return_value=SimpleNamespace(YoutubeDL=Downloader),
        ),
        patch.object(media_link_capture.subprocess, "run", side_effect=convert),
        patch.object(media_link_capture, "converted_duration", return_value=3001),
    ):
        if failure:
            with pytest.raises(AcquisitionBlocked):
                media_link_capture.capture(inputs, outputs)
            assert not list(outputs.iterdir())
        else:
            media_link_capture.capture(inputs, outputs)
            value, data = media_link_worker.verify(tmp_path, SPEC)
            assert data == b"converted-file" and value["converted_duration_ms"] == 3001
            assert value["source_coverage_verified"] is False
    assert calls.count("metadata") == 1 and calls.count("v") == 1 and calls.count("a") == 1
    assert calls.count("conversion") == (failure is None)


@pytest.mark.parametrize("selected", [[], False, "", {}, {"unexpected": []}])
def test_empty_or_invalid_pair_never_enters_implicit_single_file_merge(selected):
    value = dict(INFO, protocol="https", requested_formats=selected)
    with pytest.raises(AcquisitionBlocked):
        checked_info(value, URL, mode="full_evidence")
