"""Shared short URLs resolve once within the original platform, before V3 runs."""

import pytest
from test_article_fetch import Reply, install

from fiber_bundle import video_source_resolution as source
from fiber_bundle.config import InvalidInput


def test_b23_redirect_is_pinned_without_downloading_video_page(monkeypatch):
    calls, closed = install(
        monkeypatch,
        [
            Reply(
                302,
                b"",
                [("Location", "https://www.bilibili.com/video/BV1AxpE6mEgD?share_source=copy")],
            )
        ],
    )
    result = source.resolve("https://b23.tv/synthetic", lambda: None)
    assert result["final_url"] == "https://www.bilibili.com/video/BV1AxpE6mEgD"
    assert result["original_url"] == "https://b23.tv/synthetic"
    assert len(calls) == len(closed) == 1
    assert len(result["redirects"]) == 2


@pytest.mark.parametrize(
    "target",
    [
        "https://127.0.0.1/secret",
        "http://www.bilibili.com/video/BV1AxpE6mEgD",
        "https://evil.com/video/BV1AxpE6mEgD",
        "https://b23.tv/synthetic",
    ],
)
def test_redirect_escape_or_loop_is_not_requested(monkeypatch, target):
    calls, closed = install(monkeypatch, [Reply(302, b"", [("Location", target)])])
    with pytest.raises(InvalidInput):
        source.resolve("https://b23.tv/synthetic", lambda: None)
    assert len(calls) == len(closed) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bilibili.com/video/BV1AxpE6mEgD?p=2",
        "https://www.youtube.com/watch?v=abcdefghijk&list=PLsynthetic",
    ],
)
def test_parts_and_playlists_do_not_silently_become_different_sources(url):
    with pytest.raises(InvalidInput):
        source.resolve(url, lambda: None)


def test_cancel_after_headers_prevents_initialization(monkeypatch):
    calls, closed = install(
        monkeypatch,
        [Reply(302, b"", [("Location", "https://www.bilibili.com/video/BV1AxpE6mEgD")])],
    )

    def guard():
        if calls:
            raise InvalidInput("intake_user_aborted")

    with pytest.raises(InvalidInput, match="user_aborted"):
        source.resolve("https://b23.tv/synthetic", guard)
    assert len(closed) == 1


@pytest.mark.parametrize(
    "url", ["https://youtu.be/abcdefghijk?si=tracking", "https://m.youtube.com/shorts/abcdefghijk"]
)
def test_youtube_canonicalization_keeps_supported_forms(url):
    assert (
        source.resolve(url, lambda: None)["final_url"]
        == "https://www.youtube.com/watch?v=abcdefghijk"
    )
