import json
import os

import pytest
from test_articles import IMAGE_URL, PNG, X_URL, current_html

from fiber_bundle.article_capture import capture, verify_capture
from fiber_bundle.article_fetch import Response
from fiber_bundle.articles import ArticleError


def fixtures(url, limit):
    if url == X_URL:
        return Response(url, "text/html; charset=utf-8", current_html())
    assert url == IMAGE_URL
    return Response(url, "image/png", PNG)


def test_capture_roundtrip_reparses_original_and_preserves_bytes(tmp_path):
    capture(X_URL, tmp_path, fixtures)
    verified = verify_capture(tmp_path, X_URL)
    assert verified.raw_html == current_html()
    assert verified.draft.images[0].data == PNG
    assert "![图片 1](images/" in verified.draft.markdown
    assert set(p.name for p in tmp_path.iterdir()) == {
        "source.html",
        "image-001.bin",
        "capture.json",
    }


def test_partial_failure_keeps_original_without_success_manifest(tmp_path):
    def failed_image(url, limit):
        if url == IMAGE_URL:
            raise ArticleError("fixture_network_failure")
        return fixtures(url, limit)

    with pytest.raises(ArticleError, match="fixture_network_failure"):
        capture(X_URL, tmp_path, failed_image)
    assert (tmp_path / "source.html").read_bytes() == current_html()
    assert not (tmp_path / "capture.json").exists()
    with pytest.raises(ArticleError):
        verify_capture(tmp_path, X_URL)


def test_retry_never_overwrites_partial_capture(tmp_path):
    sentinel = tmp_path / "source.html"
    sentinel.write_bytes(b"original sentinel")
    with pytest.raises(ArticleError, match="not_empty"):
        capture(X_URL, tmp_path, fixtures)
    assert sentinel.read_bytes() == b"original sentinel"


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("source_url", "https://x.com/another/status/123"),
        ("images", []),
        ("final_url", "https://127.0.0.1/"),
        ("content_type", None),
    ],
)
def test_invalid_manifest_rejected(tmp_path, field, value):
    capture(X_URL, tmp_path, fixtures)
    path = tmp_path / "capture.json"
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ArticleError):
        verify_capture(tmp_path, X_URL)


@pytest.mark.parametrize(
    "field,value",
    [
        ("index", 2),
        ("url", "https://pbs.twimg.com/wrong.png"),
        ("bytes", True),
        ("sha256", "0" * 64),
        ("content_type", "image/jpeg"),
    ],
)
def test_image_manifest_bound_to_reparsed_html(tmp_path, field, value):
    capture(X_URL, tmp_path, fixtures)
    path = tmp_path / "capture.json"
    manifest = json.loads(path.read_text())
    manifest["images"][0][field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ArticleError):
        verify_capture(tmp_path, X_URL)


@pytest.mark.parametrize("name", ["source.html", "image-001.bin"])
def test_mutated_bytes_fail_hash_validation(tmp_path, name):
    capture(X_URL, tmp_path, fixtures)
    path = tmp_path / name
    path.write_bytes(path.read_bytes() + b"mutation")
    with pytest.raises(ArticleError, match="digest"):
        verify_capture(tmp_path, X_URL)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_nonregular_or_linked_capture_not_read(tmp_path, kind):
    output = tmp_path / "output"
    output.mkdir()
    capture(X_URL, output, fixtures)
    image = output / "image-001.bin"
    image.unlink()
    external = tmp_path / "external.png"
    external.write_bytes(PNG)
    if kind == "symlink":
        image.symlink_to(external)
    elif kind == "hardlink":
        os.link(external, image)
    else:
        os.mkfifo(image)
    with pytest.raises(ArticleError):
        verify_capture(output, X_URL)
    assert external.read_bytes() == PNG


def test_no_worker_authored_markdown_is_trusted(tmp_path):
    capture(X_URL, tmp_path, fixtures)
    (tmp_path / "note.md").write_text("worker claimed this is valid")
    with pytest.raises(ArticleError, match="unexpected"):
        verify_capture(tmp_path, X_URL)


def test_disk_write_failure_never_creates_success_manifest(tmp_path, monkeypatch):
    import fiber_bundle.article_capture as module

    def fail_sync(_):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(module, "sync_directory", fail_sync)
    with pytest.raises(OSError):
        capture(X_URL, tmp_path, fixtures)
    assert not (tmp_path / "capture.json").exists()
