"""Shared image-message parsing through capture verification and automatic intake review."""

import json

import pytest
from test_article_fetch import Reply, install
from test_articles import PNG
from test_channel_intake import count, enable, receive, records
from test_channel_intake import entry as entry
from test_channel_intake_content import grant_original
from test_wechat_image_messages import IMAGE_URLS, URL, image_message_html
from test_weixin_inbox import binding as binding

from fiber_bundle.article_capture import capture, verify_capture
from fiber_bundle.article_fetch import Response
from fiber_bundle.articles import ArticleError
from fiber_bundle.channels import intake_extraction as extraction
from fiber_bundle.channels import intake_processing as processing
from fiber_bundle.channels import intake_reviews
from fiber_bundle.config import InvalidInput


def test_manual_capture_and_host_reparse_preserve_picture_order(tmp_path):
    raw = image_message_html()
    calls = []

    def fetch(url, limit):
        calls.append(url)
        return Response(url, "text/html", raw) if url == URL else Response(url, "image/png", PNG)

    capture(URL, tmp_path, fetch)
    verified = verify_capture(tmp_path, URL)
    assert calls == [URL, *IMAGE_URLS]
    assert verified.raw_html == raw
    assert [i.index for i in verified.draft.images] == [1, 2]
    assert "图1是入口,图2是出口。" in verified.draft.markdown
    # Host reparsing still binds the gallery to exactly the captured image bytes.
    (tmp_path / "image-002.bin").write_bytes(PNG + b"changed")
    with pytest.raises(ArticleError, match="digest"):
        verify_capture(tmp_path, URL)


def test_intake_gallery_requires_review_before_portable_note_publication(
    config, entry, monkeypatch
):
    enable(config, entry, fetch_articles=True, download_limit=3)
    batch = receive(config, entry, text=URL)
    processing.advance(config, entry[0]["id"])
    calls, closed = install(
        monkeypatch,
        [
            Reply(200, image_message_html(), [("Content-Type", "text/html; charset=utf-8")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
            Reply(200, PNG + b"second", [("Content-Type", "image/png")]),
        ],
    )
    assert extraction.advance(config, entry[0]["id"])
    assert len(calls) == len(closed) == 3
    assert not extraction.advance(config, entry[0]["id"])
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert link["state"] == "review_required" and link["metadata"]["image_count"] == 2
    assert link["metadata"]["ocr"] is False
    grant_original(config, entry)
    packet = intake_reviews.packet(config, entry[0]["id"], batch, 0)
    assert "图1是入口,图2是出口。" in packet["markdown"]
    with pytest.raises(InvalidInput, match="review_required"):
        intake_reviews.publish(config, entry[0]["id"], batch, 0, "not-reviewed")
    review = intake_reviews.review(
        config, entry[0]["id"], batch, 0, packet["digest"], "核对两张原图及配文"
    )
    publication = intake_reviews.publish(config, entry[0]["id"], batch, 0, review["id"])
    path = config.project_root / "test vault/temp_vault1" / publication["relative_path"]
    note = (path / "note.md").read_text()
    images = sorted((path / "images").iterdir())
    assert len(images) == 2
    assert [i.read_bytes() for i in images] == [PNG, PNG + b"second"]
    assert all(f"images/{i.name}" in note for i in images)
    assert note.index("**图1**") < note.index("**图2**") < note.index("图1是入口")
    assert count(config, "model_calls") == count(config, "execution_runs") == 0


def test_incomplete_gallery_keeps_original_and_offers_no_full_draft(config, entry, monkeypatch):
    enable(config, entry, fetch_articles=True, download_limit=2)
    batch = receive(config, entry, text=URL)
    processing.advance(config, entry[0]["id"])
    calls, _ = install(
        monkeypatch,
        [
            Reply(200, image_message_html(), [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    extraction.advance(config, entry[0]["id"])
    assert len(calls) == 2
    assert not extraction.advance(config, entry[0]["id"])
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert link["state"] == "needs_review" and "receipt" not in link
    assert count(config, "receipts") == 1
    grant_original(config, entry)
    with pytest.raises(InvalidInput):
        intake_reviews.packet(config, entry[0]["id"], batch, 0)


def test_worker_copy_plan_can_parse_gallery_in_isolation(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from fiber_bundle import article_capture

    source = Path(article_capture.__file__).parent
    root = tmp_path / "fiber_bundle"
    root.mkdir()
    for line in (source / "article_executor/Dockerfile").read_text().splitlines():
        if not line.startswith("COPY "):
            continue
        *names, destination = line.split()[1:]
        target = root / destination
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(source / name, target / Path(name).name)
    code = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from fiber_bundle.articles import parse_article;"
        "a=parse_article(sys.stdin.buffer.read(),sys.argv[2]);"
        "assert len(a.images)==2 and a.marker.startswith('wechat:image-message:')"
    )
    subprocess.run(
        [sys.executable, "-I", "-c", code, str(tmp_path), URL],
        input=image_message_html(),
        cwd=tmp_path,
        check=True,
        capture_output=True,
        timeout=10,
    )


def test_component_check_detects_changed_image_message_parser(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from fiber_bundle import management_inventory

    source = Path(management_inventory.__file__).parent / "vendor"
    shutil.copytree(source, tmp_path / "vendor", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(management_inventory, "__file__", str(tmp_path / "inventory.py"))
    assert management_inventory.check_components()["state"] == "matched"
    (tmp_path / "vendor/wechat_image_message.py").write_text("changed extractor")
    result = management_inventory.check_components()
    assert result["state"] == "changed"
    assert {"name": "wechat_image_message.py", "state": "changed"} in result["checks"]
