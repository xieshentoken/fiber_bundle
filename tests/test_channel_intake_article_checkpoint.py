"""Bounded private acquisition state is reusable only by its exact source task."""

import sqlite3
import stat
from pathlib import Path

import pytest
from test_articles import PNG

from fiber_bundle.article_fetch import Response
from fiber_bundle.channels import intake_article_checkpoint as checkpoints
from fiber_bundle.config import InvalidInput

SOURCE = "https://mp.weixin.qq.com/s/synthetic"
IMAGE = "https://mmbiz.qpic.cn/synthetic.png?signature=private"
BODY = Response(SOURCE, "text/html; charset=utf-8", b"<html>synthetic</html>", (SOURCE,))
PHOTO = Response(IMAGE, "image/png", PNG, (IMAGE,))


def checkpoint(
    root: Path,
    *,
    identity: str = "feishu",
    batch: str = "batch",
    index: int = 0,
    revision: int = 1,
    target: str = "vault",
    source: str = SOURCE,
) -> checkpoints.ArticleCheckpoint:
    return checkpoints.ArticleCheckpoint(
        root,
        identity=identity,
        batch=batch,
        index=index,
        revision=revision,
        target_vault_id=target,
        source=source,
    )


def test_reopened_checkpoint_retains_one_resource_and_is_private(tmp_path):
    task = checkpoint(tmp_path)
    assert task.load_source() is None
    assert not list(tmp_path.iterdir())
    task.save_source(BODY)
    task.save_image(BODY, IMAGE, PHOTO)
    task.save_image(BODY, IMAGE, PHOTO)

    database = next(tmp_path.rglob("*.sqlite3"))
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert "signature" not in str(database.relative_to(tmp_path))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM responses").fetchone() == (2,)

    reopened = checkpoint(tmp_path)
    assert reopened.load_source() == BODY
    assert reopened.load_image(BODY, IMAGE) == PHOTO
    assert reopened.load_image(BODY, IMAGE + "2") is None
    reopened.clear()
    assert checkpoint(tmp_path).load_source() is None


@pytest.mark.parametrize(
    "binding",
    [
        {"identity": "another-feishu"},
        {"batch": "another-batch"},
        {"index": 1},
        {"revision": 2},
        {"target": "another-vault"},
        {"source": SOURCE + "2"},
    ],
)
def test_binding_mismatch_cannot_reuse_source_or_images(tmp_path, binding):
    original = checkpoint(tmp_path)
    original.save_source(BODY)
    original.save_image(BODY, IMAGE, PHOTO)
    unrelated = checkpoint(tmp_path, **binding)
    assert unrelated.load_source() is None
    assert unrelated.load_image(BODY, IMAGE) is None


def test_changed_source_does_not_mix_generations_or_let_stale_owner_clear(tmp_path):
    original = checkpoint(tmp_path)
    original.save_source(BODY)
    original.save_image(BODY, IMAGE, PHOTO)
    newer_body = Response(SOURCE, "text/html; charset=utf-8", b"<html>changed</html>", (SOURCE,))
    current = checkpoint(tmp_path)
    current.save_source(newer_body)
    original.save_image(BODY, IMAGE, PHOTO)
    original.clear()
    assert checkpoint(tmp_path).load_source() == newer_body
    assert current.load_image(newer_body, IMAGE) is None
    assert current.load_image(BODY, IMAGE) is None


def test_corrupt_bytes_do_not_become_cached_content(tmp_path):
    original = checkpoint(tmp_path)
    original.save_source(BODY)
    original.save_image(BODY, IMAGE, PHOTO)
    database = next(tmp_path.rglob("*.sqlite3"))
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE responses SET body=? WHERE resource<>''", (b"x" * len(PNG),))
    reopened = checkpoint(tmp_path)
    assert reopened.load_source() == BODY
    assert reopened.load_image(BODY, IMAGE) is None


def test_idle_expiration_and_capacity_keep_only_recent_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoints, "_TASKS", 2)
    first = checkpoint(tmp_path)
    second = checkpoint(tmp_path, index=1)
    third = checkpoint(tmp_path, index=2)
    first.save_source(BODY)
    second.save_source(BODY)
    third.save_source(BODY)
    assert first.load_source() is None
    assert second.load_source() == third.load_source() == BODY

    database = next(tmp_path.rglob("*.sqlite3"))
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE responses SET updated=0")
    assert third.load_source() is None
    first.save_source(BODY)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM responses").fetchone() == (1,)


def test_cache_fails_closed_for_unsafe_path(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "runtime").symlink_to(other, target_is_directory=True)
    task = checkpoint(tmp_path)
    with pytest.raises(InvalidInput, match="^intake_checkpoint_unavailable$"):
        task.save_source(BODY)
    assert not list(other.iterdir())


def test_locked_cache_does_not_block_ordinary_acquisition(tmp_path):
    task = checkpoint(tmp_path)
    task.save_source(BODY)
    database = next(tmp_path.rglob("*.sqlite3"))
    with sqlite3.connect(database) as connection:
        connection.execute("BEGIN EXCLUSIVE")
        assert task.load_source() is None
        task.save_source(BODY)
        task.save_image(BODY, IMAGE, PHOTO)
    assert task.load_source() == BODY


def test_overflow_is_optional_and_does_not_remove_existing_cache(tmp_path, monkeypatch):
    task = checkpoint(tmp_path)
    task.save_source(BODY)
    monkeypatch.setattr(checkpoints, "_TASK_BYTES", len(PNG))
    task.save_image(BODY, IMAGE, PHOTO)
    assert task.load_source() == BODY
    assert task.load_image(BODY, IMAGE) is None
