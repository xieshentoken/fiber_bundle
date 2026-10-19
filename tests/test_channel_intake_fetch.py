"""Literal adapters and resource binding, with only HTTPS transport substituted."""

import json

import pytest
from test_article_fetch import Reply, install
from test_articles import PNG, TEXT

from fiber_bundle.channels import intake_contract, intake_fetch
from fiber_bundle.config import InvalidInput


def youtube_page(seconds="30"):
    return (
        "<script>ytInitialPlayerResponse="
        + json.dumps(
            {
                "playabilityStatus": {"status": "OK"},
                "videoDetails": {
                    "videoId": "abcdefghijk",
                    "title": "合成",
                    "lengthSeconds": seconds,
                },
                "captions": {
                    "playerCaptionsTracklistRenderer": {
                        "captionTracks": [
                            {
                                "languageCode": "zh-Hans",
                                "baseUrl": "https://www.youtube.com/api/timedtext?v=abcdefghijk",
                            }
                        ]
                    }
                },
            }
        )
        + ";</script>"
    ).encode()


def test_caption_redirect_cannot_change_video_on_allowed_host(monkeypatch):
    calls, _ = install(
        monkeypatch,
        [
            Reply(200, youtube_page(), [("Content-Type", "text/html")]),
            Reply(302, b"", [("Location", "https://www.youtube.com/api/timedtext?v=other-video")]),
        ],
    )
    with pytest.raises(InvalidInput, match="subtitle_binding_invalid"):
        intake_fetch.acquire(
            "https://www.youtube.com/watch?v=abcdefghijk",
            intake_contract.Settings(fetch_subtitles=True).model_dump(),
            lambda url: None,
            lambda: None,
        )
    assert len(calls) == 2  # The redirected caption request was never made.


def test_video_limit_does_not_expand_with_automatic_mode(monkeypatch):
    calls, _ = install(
        monkeypatch, [Reply(200, youtube_page("61"), [("Content-Type", "text/html")])]
    )
    with pytest.raises(InvalidInput, match="duration_over_limit"):
        intake_fetch.acquire(
            "https://www.youtube.com/watch?v=abcdefghijk",
            intake_contract.Settings(fetch_subtitles=True).model_dump(),
            lambda url: None,
            lambda: None,
        )
    assert len(calls) == 1


def test_bilibili_other_part_never_silently_fetches_first_part(monkeypatch):
    calls, _ = install(
        monkeypatch, [Reply(200, b"<html>synthetic</html>", [("Content-Type", "text/html")])]
    )
    with pytest.raises(InvalidInput, match="single_resource_required"):
        intake_fetch.acquire(
            "https://www.bilibili.com/video/BV1234567890?p=2",
            intake_contract.Settings(fetch_subtitles=True).model_dump(),
            lambda url: None,
            lambda: None,
        )
    assert len(calls) == 1


def test_repeated_article_image_preserves_both_positions_with_one_download(monkeypatch):
    raw = (
        '<h1 id="activity-name">合成图文</h1><div id="js_content"><p>' + TEXT + "</p>"
        '<img data-src="https://mmbiz.qpic.cn/synthetic.png"><p>图中段落</p>'
        '<img data-src="https://mmbiz.qpic.cn/synthetic.png"></div>'
    ).encode()
    calls, _ = install(
        monkeypatch,
        [
            Reply(200, raw, [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    reservations = []
    result = intake_fetch.acquire(
        "https://mp.weixin.qq.com/s/synthetic",
        intake_contract.Settings(fetch_articles=True).model_dump(),
        reservations.append,
        lambda: None,
    )
    assert len(calls) == len(reservations) == 2
    assert result.metadata["image_count"] == 2
    assert result.files["note.md"].decode().count("![图片") == 2
