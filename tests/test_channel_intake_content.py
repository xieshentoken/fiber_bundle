"""Bounded synthetic HTTP -> parser -> Store -> exact review -> exclusive note publication."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_article_fetch import Reply, install
from test_articles import PNG, TEXT, X_URL, current_html
from test_channel_intake import count, enable, receive, records
from test_channel_intake import entry as entry
from test_weixin_inbox import binding as binding

from fiber_bundle import auth, database
from fiber_bundle.channels import intake_extraction as extraction
from fiber_bundle.channels import intake_processing as processing
from fiber_bundle.channels import intake_receipts, intake_reviews
from fiber_bundle.channels import store as channels
from fiber_bundle.config import IntegrityError, InvalidInput


def grant_original(config, entry):
    with database.connect(config.state_root) as main:
        auth.set_grant(
            main, entry[1].client_id, entry[1].vault_id, query=True, original=True, add=True
        )


def test_new_long_wechat_forward_automatically_saves_indexes_and_deduplicates(
    config, entry, monkeypatch
):
    from test_automatic_chat import drain
    from test_publication_layout import activate

    from fiber_bundle import automatic_ingestion, automatic_policy, search, vault_save_contract

    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)
    grant_original(config, entry)
    settings = vault_save_contract.settings_wire(vault_save_contract.unified_settings())
    settings.pop("layout_mode")
    activate(config, entry[1].vault_id, **settings)
    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            entry[1].vault_id,
            0,
            automatic_policy.Policy(
                policy_version=2,
                enabled=True,
                direct_to_inbox=True,
                client_id=entry[1].client_id,
                allowed_kinds=["intake"],
            ),
        )
    enable(config, entry, policy_version=2, fetch_articles=True, store_limit=0, download_limit=0)
    raw = (
        '<h1 id="activity-name">Long WeChat</h1><div id="js_content">'
        + TEXT
        + "".join(
            f'<p>image-{i}</p><img src="https://mmbiz.qpic.cn/{i % 69}.png">' for i in range(86)
        )
        + "<p>最后的合成末段</p></div>"
    ).encode()
    receive(config, entry, text="https://mp.weixin.qq.com/s/long-synthetic")
    processing.advance(config, entry[0]["id"])
    calls, _ = install(
        monkeypatch,
        [
            Reply(200, raw, [("Content-Type", "text/html")]),
            *[Reply(200, PNG, [("Content-Type", "image/png")]) for _ in range(69)],
        ],
    )
    extraction.advance(config, entry[0]["id"])
    assert len(calls) == 70
    drain(config)
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert link["metadata"]["image_count"] == 86 and link["publication"]["state"] == "published"
    path = config.project_root / "test vault/temp_vault1" / link["publication"]["entry_note_path"]
    assert "最后的合成末段" in path.read_text()
    with database.connect(config.state_root) as db:
        assert db.execute("select state from automatic_jobs").fetchone()[0] == "complete"
        assert search.vault_search(
            config, db, entry[1].client_id, "Long WeChat", [entry[1].vault_id]
        )["results"]
        before = db.execute("select count(*) from receipts").fetchone()[0]
    assert not extraction.advance(config, entry[0]["id"])
    drain(config)
    assert count(config, "receipts") == before and count(config, "model_calls") == 0


def test_x_all_images_literal_review_publish_no_model_and_manual_edit_protection(
    config, entry, monkeypatch, http
):
    enable(config, entry, fetch_articles=True, download_limit=5)
    batch = receive(config, entry, text=X_URL)
    processing.advance(config, entry[0]["id"])
    calls, closed = install(
        monkeypatch,
        [
            Reply(200, current_html(), [("Content-Type", "text/html; charset=utf-8")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    assert extraction.advance(config, entry[0]["id"])
    assert len(calls) == len(closed) == 2
    assert not extraction.advance(config, entry[0]["id"])
    assert json.loads(records(config, entry)[0]["value"])["links"][0]["state"] == "review_required"
    with pytest.raises(auth.Denied):
        intake_reviews.packet(config, entry[0]["id"], batch, 0)
    grant_original(config, entry)
    prefix = f"/admin/channels/{entry[0]['id']}/intake/batches/{batch}/links/0"
    packet = http.get(prefix).json()
    assert TEXT in packet["markdown"] and "images/" in packet["markdown"]
    with pytest.raises(InvalidInput, match="review_required"):
        intake_reviews.publish(config, entry[0]["id"], batch, 0, "not-reviewed")
    response = http.post(
        prefix + "/review",
        json={
            "digest": packet["digest"],
            "reason": "逐段核对合成资料与图片",
            "source_reviewed": True,
            "text_reviewed": True,
            "images_reviewed": True,
        },
    )
    assert response.status_code == 200, response.text
    review = response.json()
    publication = intake_reviews.publish(config, entry[0]["id"], batch, 0, review["id"])
    assert intake_reviews.publish(config, entry[0]["id"], batch, 0, review["id"]) == publication
    path = config.project_root / "test vault/temp_vault1" / publication["relative_path"]
    assert TEXT in (path / "note.md").read_text()
    assert next((path / "images").iterdir()).read_bytes() == PNG
    assert http.get(prefix + "/assets/source.html").content == current_html()
    originals = http.get(f"/admin/channels/{entry[0]['id']}/intake/batches/{batch}/originals")
    assert originals.status_code == 200 and originals.json()["assets"]
    (path / "note.md").write_text("人工编辑保留")
    with pytest.raises(IntegrityError):
        intake_reviews.publish(config, entry[0]["id"], batch, 0, review["id"])
    assert (path / "note.md").read_text() == "人工编辑保留"
    assert count(config, "model_calls") == count(config, "execution_runs") == 0


def test_short_link_redirect_rechecked_before_second_request(config, entry, monkeypatch):
    enable(config, entry, fetch_articles=True, download_limit=3)
    receive(config, entry, text="https://t.co/synthetic")
    processing.advance(config, entry[0]["id"])
    calls, _ = install(
        monkeypatch, [Reply(302, b"", [("Location", "https://unapproved.org/private")])]
    )
    extraction.advance(config, entry[0]["id"])
    assert len(calls) == 1
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert link["state"] == "needs_review"
    assert count(config, "receipts") == 1


def test_missing_image_never_offers_full_draft_or_retries_automatically(config, entry, monkeypatch):
    enable(config, entry, fetch_articles=True, download_limit=1)
    batch = receive(config, entry, text=X_URL)
    processing.advance(config, entry[0]["id"])
    calls, _ = install(monkeypatch, [Reply(200, current_html(), [("Content-Type", "text/html")])])
    extraction.advance(config, entry[0]["id"])
    assert len(calls) == 1
    assert not extraction.advance(config, entry[0]["id"])
    with pytest.raises((InvalidInput, auth.Denied)):
        intake_reviews.packet(config, entry[0]["id"], batch, 0)
    assert count(config, "receipts") == 1


def test_optional_receipt_unknown_is_consumed_once_and_does_not_repeat(config, entry):
    enable(config, entry, receipt=True, outbound_limit=2)
    receive(config, entry)
    processing.advance(config, entry[0]["id"])
    calls = []

    def sender(owner, body, guard):
        guard()
        calls.append(body)
        return {"category": "transport_exception"}

    adapter = SimpleNamespace(_fiber_send_receipt=sender)
    asyncio.run(intake_receipts.deliver(config, entry[0]["id"], adapter))
    asyncio.run(intake_receipts.deliver(config, entry[0]["id"], adapter))
    assert len(calls) == 1
    assert json.loads(records(config, entry)[0]["value"])["receipt_state"] == "unknown"
    with channels.readonly(config) as side:
        from fiber_bundle.channels.intake_repository import consumed

        assert consumed(side, entry[0]["id"])["outbound"] == 1


def test_video_existing_youtube_subtitles_keep_language_and_gaps(config, entry, monkeypatch):
    enable(config, entry, fetch_subtitles=True, download_limit=4)
    url = "https://www.youtube.com/watch?v=abcdefghijk"
    receive(config, entry, text=url)
    processing.advance(config, entry[0]["id"])
    info = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {
            "videoId": "abcdefghijk",
            "title": "合成字幕视频",
            "lengthSeconds": "30",
            "shortDescription": "合成说明",
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
    page = ("<script>var ytInitialPlayerResponse = " + json.dumps(info) + ";</script>").encode()
    calls, _ = install(
        monkeypatch,
        [
            Reply(200, page, [("Content-Type", "text/html")]),
            Reply(
                200,
                "WEBVTT\n\n00:00.000 --> 00:02.000\n合成字幕\n".encode(),
                [("Content-Type", "text/vtt")],
            ),
        ],
    )
    extraction.advance(config, entry[0]["id"])
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert link["state"] == "review_required"
    assert link["metadata"]["language"] == "zh-Hans"
    assert link["metadata"]["gaps"] == [{"start_ms": 2000, "end_ms": 30000}]
    assert link["metadata"]["asr_called"] is False
    assert all("timedtext" in call[2] or "watch" in call[2] for call in calls)


def test_bilibili_metadata_and_existing_json_subtitles(config, entry, monkeypatch):
    enable(config, entry, fetch_subtitles=True, download_limit=4)
    url = "https://www.bilibili.com/video/BV1234567890"
    receive(config, entry, text=url)
    processing.advance(config, entry[0]["id"])
    page = (
        "<script>window.__INITIAL_STATE__="
        + json.dumps(
            {
                "videoData": {
                    "bvid": "BV1234567890",
                    "cid": 123,
                    "duration": 20,
                    "title": "合成 B 站字幕",
                    "desc": "合成",
                }
            }
        )
        + ";</script>"
    ).encode()
    player = json.dumps(
        {
            "code": 0,
            "data": {
                "subtitle": {
                    "subtitles": [
                        {
                            "lan": "zh-CN",
                            "subtitle_url": "https://aisubtitle.hdslb.com/bfs/subtitle/synthetic.json",
                        }
                    ]
                }
            },
        }
    ).encode()
    subtitle = json.dumps({"body": [{"from": 0, "to": 2, "content": "合成 B 站已有字幕"}]}).encode()
    calls, _ = install(
        monkeypatch,
        [
            Reply(200, page, [("Content-Type", "text/html")]),
            Reply(200, player, [("Content-Type", "application/json")]),
            Reply(200, subtitle, [("Content-Type", "application/json")]),
        ],
    )
    extraction.advance(config, entry[0]["id"])
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert link["state"] == "review_required"
    assert link["metadata"]["cues"][0]["text"] == "合成 B 站已有字幕"
    assert len(calls) == 3


def test_no_subtitles_stays_metadata_and_never_calls_asr(config, entry, monkeypatch):
    enable(config, entry, fetch_subtitles=True, download_limit=4)
    url = "https://www.youtube.com/watch?v=abcdefghijk"
    receive(config, entry, text=url)
    processing.advance(config, entry[0]["id"])
    info = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {
            "videoId": "abcdefghijk",
            "title": "合成无字幕视频",
            "lengthSeconds": "30",
        },
    }
    page = ("<script>var ytInitialPlayerResponse = " + json.dumps(info) + ";</script>").encode()
    calls, _ = install(monkeypatch, [Reply(200, page, [("Content-Type", "text/html")])])
    extraction.advance(config, entry[0]["id"])
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert (
        link["state"] == "metadata_only"
        and link["metadata"]["reason"] == "intake_subtitles_unavailable"
    )
    assert len(calls) == 1 and count(config, "model_calls") == 0


def test_backup_restore_disables_policy_but_keeps_consumption(config, entry, tmp_path):
    from fiber_bundle import backup_bundle, backup_restore
    from fiber_bundle.channels import intake_repository
    from fiber_bundle.config import load_config

    enable(config, entry)
    receive(config, entry)
    processing.advance(config, entry[0]["id"])
    backup_bundle.create(config, tmp_path / "backup", "synthetic-only-password")
    backup_restore.restore(tmp_path / "backup", tmp_path / "restored", "synthetic-only-password")
    target = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    with channels.readonly(target) as side:
        assert intake_repository.policy(side, entry[0]["id"])["settings"]["enabled"] is False
        assert intake_repository.consumed(side, entry[0]["id"])["store"] == 1


def test_http_cas_csrf_and_no_implicit_execution(config, entry, http):
    from fiber_bundle.channels import intake_contract

    prefix = f"/admin/channels/{entry[0]['id']}/intake"
    settings = intake_contract.Settings(
        target_vault_id=entry[1].vault_id, enabled=True, store_limit=3
    ).model_dump()
    response = http.put(prefix, json={"expected_revision": 0, "settings": settings})
    assert response.status_code == 200
    assert http.put(prefix, json={"expected_revision": 0, "settings": settings}).status_code == 409
    headers = dict(http.headers)
    http.headers.pop("X-CSRF-Token")
    assert http.put(prefix, json={"expected_revision": 1, "settings": settings}).status_code == 403
    http.headers.update(headers)
    assert count(config, "receipts") == count(config, "model_calls") == 0


def test_switch_withdrawal_during_fetch_keeps_only_original_and_consumed_budget(
    config, entry, monkeypatch
):
    from fiber_bundle import article_fetch

    enable(config, entry, fetch_articles=True, download_limit=3)
    receive(config, entry, text=X_URL)
    processing.advance(config, entry[0]["id"])

    def fetch(url, limit, *, validate):
        validate(url)
        enable(config, entry, enabled=False)
        return article_fetch.Response(url, "text/html", current_html(), (url,))

    monkeypatch.setattr(article_fetch, "fetch_public", fetch)
    extraction.advance(config, entry[0]["id"])
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert link["state"] == "needs_review" and "receipt" not in link
    assert count(config, "receipts") == 1
    with channels.readonly(config) as side:
        from fiber_bundle.channels.intake_repository import consumed

        assert consumed(side, entry[0]["id"])["download"] == 1


def test_receipt_reconciliation_waits_for_actual_process_exit(config, entry):
    from fiber_bundle import feishu_runtime
    from fiber_bundle.channels import legacy

    enable(config, entry, receipt=True, outbound_limit=2)
    batch = receive(config, entry)
    processing.advance(config, entry[0]["id"])
    adapter = SimpleNamespace(_fiber_send_receipt=lambda *args: {"category": "transport_exception"})
    asyncio.run(intake_receipts.deliver(config, entry[0]["id"], adapter))
    path = config.state_root / entry[0]["settings_path"]
    path.write_text(json.dumps({**json.loads(path.read_text()), "enabled": False}))
    with feishu_runtime.Instance(legacy.home(config, entry[0]), platform="feishu"):
        with pytest.raises(InvalidInput, match="stop_before_recovery"):
            intake_receipts.reconcile(config, entry[0]["id"], batch, "not_sent")
    assert (
        intake_receipts.reconcile(config, entry[0]["id"], batch, "not_sent")["replay"] == "disabled"
    )
