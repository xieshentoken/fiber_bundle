import hashlib
from dataclasses import asdict

import pytest

from fiber_bundle import auth, database, note_identities, note_search_build
from fiber_bundle.inputs import canonical
from fiber_bundle.knowledge_graph import catalog, jobs, projection, vectors

from .support import result, span
from .test_lifecycle import small as small


def groups(config, prepared, worker_policy, cache, *, check, cancelled):
    check()
    output = result(prepared.request, with_entities=False)
    output["mocs"] = [
        {
            "title": title,
            "note_ids": [n.note_id for n in prepared.request.notes],
            "evidence": [asdict(span(n, n.title)) for n in prepared.request.notes],
            "method": prepared.request.engine.algorithm,
        }
        for title in ("供应商资料", "压力资料")
    ]
    raw = canonical(output).encode()
    return vectors.Build(catalog.decode_result(raw, prepared.request), raw, {}, 0)


def test_cross_batch_groups_review_original_links_and_scope_revocation(small, monkeypatch):
    config, cid, vid, selected, token, *_ = small
    root = config.project_root / "test vault/temp_vault1"
    first = root / "n0.md"
    first.write_text(first.read_text() + "\n[[n1]]\n")
    refreshed = note_identities.run(
        config, token, vid, "track", "n0.md", hashlib.sha256(first.read_bytes()).hexdigest()
    )
    from fiber_bundle.knowledge_graph.intake import Selection

    selected = (
        Selection(refreshed["note_identity"], hashlib.sha256(first.read_bytes()).hexdigest()),
        *selected[1:],
    )
    monkeypatch.setattr(jobs.worker, "run_build", groups)
    job1 = jobs.submit(config, cid, vid, selected[:2])
    jobs.execute(config, cid, vid, job1["id"])
    job2 = jobs.submit(config, cid, vid, selected[1:])
    jobs.execute(config, cid, vid, job2["id"])
    note_search_build.build(config, cid, vid)
    main = database.connect(config.state_root)
    value = projection.read(config, main, cid, vid)
    stats = value["statistics"]
    assert (
        stats["registered_notes"]
        == stats["analyzed_recorded"]
        == stats["analyzed_current_visible"]
        == 3
    )
    assert stats["displayed_groups"] == 2 and stats["explicit_links"] == 1
    middle = selected[1].note_id
    assert (
        len(
            [
                e
                for e in value["edges"]
                if middle in (e["source"], e["target"]) and e["kind"] == "suggested"
            ]
        )
        == 2
    )
    assert {c["job_id"] for c in value["candidates"].values()} == {job1["id"], job2["id"]}
    candidate = next(c for c in value["candidates"].values() if c["job_id"] == job1["id"])
    catalog.review(
        config,
        cid,
        vid,
        candidate["id"],
        configuration=candidate["configuration"],
        revision=0,
        state="accepted",
        reason="合成来源已核对",
        job_id=job1["id"],
    )
    reviewed = projection.read(config, main, cid, vid)
    assert any(e["kind"] == "accepted" for e in reviewed["edges"])
    detail = projection.detail(config, main, cid, vid, selected[0].note_id)
    assert detail["path"] == "n0.md" and "2.4 MPa" in detail["excerpt"]
    assert projection.search(config, main, cid, vid, "青松")
    auth.set_grant(main, cid, vid, query=False, original=False, add=True)
    with pytest.raises(auth.Denied):
        projection.read(config, main, cid, vid)
    auth.set_grant(main, cid, vid, query=True, original=False, add=True)
    value = projection.read(config, main, cid, vid)
    assert (
        value["statistics"]["analyzed_current_visible"] == 0
        and value["statistics"]["explicit_links"] == 1
    )
    assert not value["candidates"]
    main.close()


def test_changed_note_invalidates_graph_until_explicit_index_refresh(small, monkeypatch):
    config, cid, vid, selected, *_ = small
    monkeypatch.setattr(jobs.worker, "run_build", groups)
    job = jobs.submit(config, cid, vid, selected)
    jobs.execute(config, cid, vid, job["id"])
    note_search_build.build(config, cid, vid)
    main = database.connect(config.state_root)
    value = projection.read(config, main, cid, vid)
    assert value["statistics"]["semantic_links"]
    (config.project_root / "test vault/temp_vault1/n0.md").write_text("# 新标题\n新内容")
    from fiber_bundle.note_search_cache import IndexUnavailable

    with pytest.raises(IndexUnavailable):
        projection.read(config, main, cid, vid)
    note_search_build.build(config, cid, vid)
    value = projection.read(config, main, cid, vid)
    assert not value["candidates"] and value["statistics"]["analyzed_current_visible"] == 2
    assert any(n["label"] == "新标题" for n in value["nodes"])
    main.close()
