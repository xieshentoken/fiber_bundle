"""A MOC write may stale the index, but source/version/review authorization stays current."""

from uuid import uuid4

import pytest

from fiber_bundle import moc_store, note_identities, note_search_build
from fiber_bundle.config import IntegrityError
from fiber_bundle.knowledge_graph import writeback

from .test_lifecycle import small as small
from .test_product import candidates as candidates


@pytest.mark.parametrize("tracked", [False, True])
def test_moc_write_with_persistent_index_completes_and_preserves_sources(
    small, candidates, tracked
):
    cfg, cid, vid, *_ = small
    target = cfg.project_root / "test vault/temp_vault1/cached-moc.md"
    expected = "absent"
    if tracked:
        target.write_text("# 人工目录\n\n保留人工说明。\n")
        import hashlib

        expected = hashlib.sha256(target.read_bytes()).hexdigest()
        note_identities.run(cfg, small[4], vid, "track", target.name, expected)
    note_search_build.build(cfg, cid, vid)
    before = {p.name: p.read_bytes() for p in target.parent.glob("n*.md")}
    mid = str(uuid4())
    packet = writeback.prepare(
        cfg,
        cid,
        vid,
        candidates[0]["id"],
        moc_id=mid,
        revision=0,
        relative=target.name,
        expected_sha256=expected,
    )
    result = writeback.apply(cfg, cid, vid, packet["id"], confirm=True)
    assert result["state"] == "completed" and target.is_file()
    assert moc_store.load(cfg, vid, mid).members
    from fiber_bundle import note_identity_store

    records = note_identity_store.records(cfg, vid)
    members = moc_store.load(cfg, vid, mid).members
    linked_paths = {r.relative_path for r in records if r.identity in members}
    for name, raw in before.items():
        expected = (
            (b'---\nmoc:\n  - "[[cached-moc]]"\n---\n' + raw) if name in linked_paths else raw
        )
        assert (target.parent / name).read_bytes() == expected
    assert writeback.apply(cfg, cid, vid, packet["id"], confirm=True)["state"] == "completed"


def test_current_source_change_still_rejects_cached_writeback(small, candidates):
    cfg, cid, vid, *_ = small
    note_search_build.build(cfg, cid, vid)
    packet = writeback.prepare(
        cfg,
        cid,
        vid,
        candidates[0]["id"],
        moc_id=str(uuid4()),
        revision=0,
        relative="blocked-moc.md",
        expected_sha256="absent",
    )
    source = next(iter(candidates[0]["paths"].values()))
    p = cfg.project_root / "test vault/temp_vault1" / source
    p.write_text(p.read_text() + "\n人工更新\n")
    with pytest.raises(IntegrityError):
        writeback.apply(cfg, cid, vid, packet["id"], confirm=True)
    assert not (p.parent / "blocked-moc.md").exists()


def test_human_edit_after_moc_projection_still_invalidates_graph_evidence(small, candidates):
    cfg, cid, vid, *_ = small
    root = cfg.project_root / "test vault/temp_vault1"
    packet = writeback.prepare(
        cfg,
        cid,
        vid,
        candidates[0]["id"],
        moc_id=str(uuid4()),
        revision=0,
        relative="topic-moc.md",
        expected_sha256="absent",
    )
    assert writeback.apply(cfg, cid, vid, packet["id"], confirm=True)["state"] == "completed"
    from fiber_bundle.knowledge_graph import catalog

    assert all(
        item["review"]["state"] != "stale" for item in catalog.browse(cfg, cid, vid)["items"]
    )
    source = next(iter(candidates[0]["paths"].values()))
    note = root / source
    note.write_text(note.read_text() + "\n用户修改正文\n")
    before = (root / "topic-moc.md").read_bytes()
    with pytest.raises(IntegrityError, match="stale"):
        writeback.apply(cfg, cid, vid, packet["id"], confirm=True)
    assert (root / "topic-moc.md").read_bytes() == before
