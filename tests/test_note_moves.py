import hashlib
import json
import os
import shutil
from unittest.mock import patch

import pytest
from test_api import add_client, permissions
from test_article_publication import completed, destination
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy

from fiber_bundle import (
    article_publication,
    auth,
    database,
    evidence_answers,
    note_index,
    note_publications,
    search,
)
from fiber_bundle.config import IntegrityError, StorageError


@pytest.fixture
def published(config, policy, article_scope):
    rid = completed(config, policy, article_scope)
    result = article_publication.publish(config, article_scope[0], rid)
    db = database.connect(config.state_root)
    auth.set_grant(db, article_scope[0], article_scope[1], query=True, original=True, add=True)
    try:
        yield db, result, destination(config, result), rid
    finally:
        db.close()


def hits(config, db, scope, query="Synthetic"):
    return search.search(config, db, scope[0], query, [scope[1]])["results"]


def tree(root):
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def move(note, name="renamed"):
    target = note.parent.parent / name
    target.parent.mkdir(parents=True, exist_ok=True)
    note.parent.rename(target)
    return target / "note.md"


def test_repeated_directory_moves_keep_provenance_without_writes(config, article_scope, published):
    db, result, note, _ = published
    original = hits(config, db, article_scope)[0]
    ledger = db.execute("SELECT * FROM article_publications").fetchone()[:]
    root = config.project_root / "test vault/temp_vault1"
    for name in ["renamed", "未分类/研究文章", "未分类/归档/再次改名"]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        note.parent.rename(target)
        note = target / "note.md"
        before = tree(root)
        current = hits(config, db, article_scope)[0]
        assert current["note_path"] == note.relative_to(root).as_posix()
        assert current["source_kind"] == "published_article"
        for field in ["sha256", "source_url", "source_version_id", "originals"]:
            assert current[field] == original[field]
        assert tree(root) == before
        assert db.execute("SELECT * FROM article_publications").fetchone()[:] == ledger
        assert not (root / result["note_path"]).exists()


@pytest.mark.parametrize("delete", ["note", "bundle"])
def test_moved_human_revision_and_deletion_never_restore(config, article_scope, published, delete):
    db, result, note, rid = published
    note = move(note, "未分类/人工改名")
    note.write_text("# Synthetic\n人工修订压力 80 psi。\n")
    answer = evidence_answers.answer(
        config, db, article_scope[0], "什么压力？", "人工", [article_scope[1]]
    )
    source = answer["sources"][0]
    assert source["source_kind"] == "user_revision"
    assert source["source_version_id"] == result["source_version_id"]
    assert source["sha256"] == hashlib.sha256(note.read_bytes()).hexdigest()
    assert answer["quotations"][0]["quote"] == "人工修订压力 80 psi。"
    if delete == "note":
        note.unlink()
    else:
        shutil.rmtree(note.parent)
    assert hits(config, db, article_scope, "人工") == []
    with pytest.raises(IntegrityError):
        article_publication.publish(config, article_scope[0], rid)
    assert not note.exists()
    assert not (config.project_root / "test vault/temp_vault1" / result["note_path"]).exists()
    assert db.execute("SELECT count(*) FROM source_versions").fetchone()[0] > 0


@pytest.mark.parametrize("copy_kind", ["unchanged", "edited", "deleted_note"])
def test_duplicate_identity_refuses_without_altering_either(
    config, article_scope, published, copy_kind
):
    db, _, note, _ = published
    target = note.parent.parent / "duplicate"
    shutil.copytree(note.parent, target)
    if copy_kind == "edited":
        (target / "note.md").write_text("Synthetic different human content")
    elif copy_kind == "deleted_note":
        (target / "note.md").unlink()
    before = tree(note.parent.parent)
    with pytest.raises(IntegrityError, match="multiple locations"):
        hits(config, db, article_scope)
    assert tree(note.parent.parent) == before


@pytest.mark.parametrize("field", ["version_id", "files"])
def test_known_marker_must_match_ledger(config, article_scope, published, field):
    db, _, note, _ = published
    note = move(note)
    marker = note.parent / note_publications.MARKER
    data = json.loads(marker.read_bytes())
    data[field] = "forged"
    marker.write_text(json.dumps(data))
    with pytest.raises(IntegrityError, match="differs from ledger"):
        hits(config, db, article_scope)


@pytest.mark.parametrize("state", ["preparing", "ready"])
def test_moved_unpublished_bundle_stays_hidden(config, article_scope, published, state):
    db, _, note, _ = published
    note = move(note)
    db.execute("UPDATE article_publications SET state=?", (state,))
    assert hits(config, db, article_scope) == []
    assert note.exists()


def test_cross_vault_copy_does_not_adopt_provenance(config, article_scope, published):
    db, _, note, _ = published
    other = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
    auth.set_grant(db, article_scope[0], other, query=True, original=True, add=True)
    target = config.project_root / "test vault/temp_vault2/copied"
    shutil.copytree(note.parent, target)
    assert search.search(config, db, article_scope[0], "Synthetic", [other])["results"] == []
    assert hits(config, db, article_scope)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "oversize"])
def test_unsafe_moved_marker_refuses(config, article_scope, published, tmp_path, kind):
    db, _, note, _ = published
    note = move(note)
    marker = note.parent / note_publications.MARKER
    data = marker.read_bytes()
    marker.unlink()
    external = tmp_path / "external-marker"
    external.write_bytes(data)
    if kind == "symlink":
        marker.symlink_to(external)
    elif kind == "hardlink":
        os.link(external, marker)
    elif kind == "fifo":
        os.mkfifo(marker)
    else:
        marker.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(StorageError):
        hits(config, db, article_scope)
    assert external.read_bytes() == data


def test_marker_change_before_delivery_refuses(config, article_scope, published):
    db, _, note, _ = published
    note = move(note)
    read = note_publications.read_marker
    calls = 0

    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            (note.parent / note_publications.MARKER).write_text("{}")
        return read(*args, **kwargs)

    with patch.object(note_publications, "read_marker", side_effect=changed):
        with pytest.raises(IntegrityError, match="before delivery"):
            hits(config, db, article_scope)


def test_markers_share_snapshot_byte_limit(config, article_scope, published):
    db, _, note, _ = published
    row = db.execute("SELECT * FROM article_publications").fetchone()
    bound = note_publications.binding(row)
    note = move(note)
    size = (note.parent / note_publications.MARKER).stat().st_size
    with pytest.raises(IntegrityError, match="byte limit"):
        note_index.snapshot(
            config.project_root / "test vault/temp_vault1",
            {row["relative_path"]},
            {row["relative_path"]},
            note_index.Limits(total_bytes=size - 1),
            publications={bound.publication_id: bound},
        )


def test_conflict_http_response_has_no_partial_evidence(http, config, article_scope, published):
    _, _, note, _ = published
    cid, headers = add_client(http)
    permissions(http, cid, article_scope[1], query=True)
    shutil.copytree(note.parent, note.parent.parent / "duplicate")
    response = http.post(
        "/v1/search",
        headers=headers,
        json={"query": "Synthetic", "vault_ids": [article_scope[1]]},
    )
    assert response.status_code == 503
    assert "Synthetic" not in response.text and "duplicate" not in response.text
