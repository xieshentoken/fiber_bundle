"""Lossless published parts, ledger authority and actual literal/index retrieval."""

import json
from unittest.mock import patch

import pytest
from test_document_human_publication import assessment
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner
from test_source_imports import request, upload
from test_source_processing import WORKERS, worker

from fiber_bundle import (
    auth,
    database,
    document_evidence,
    document_parts,
    document_publication,
    document_review,
    note_identities,
    note_inventory,
    note_search_build,
    objects,
    publication_files,
    search,
    source_import_execution,
    source_processing_policy,
)
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.document_split import SplitRequired, split


@pytest.mark.parametrize(
    "structure",
    [
        "---\ntitle: 保留\ntext: |\n  一\n\n  二\n---\n\n",
        "```python\nfirst\n\nsecond\n```\n\n",
        "~~~\nfirst\n\nsecond\n~~~~\n\n",
        "| A | B |\n|---|---|\n| 一 | 二 |\n\n",
        "Setext heading\n==============\n\nparagraph\n\n",
        "- first\n\n  paragraph\n\n- second\n\n",
        "> quote\n> first\n\n> second\n\n",
        "    first\n\n\n    second\n\n",
        "$$\nx + y\n\nz + w\n$$\n\n",
    ],
)
def test_split_structure_and_unicode_bytes_intact(structure):
    before = "" if structure.startswith("---\n") else "# 起始\n\n前言保持。\n\n"
    after = "# 末尾\n\n结果保持。\n"
    raw = (before + structure + after).encode()
    maximum = max(len(structure.encode()), len(before.encode()), len(after.encode()))
    parts = split(raw, maximum, len(raw))
    assert len(parts) > 1 and b"".join(parts) == raw
    assert all(0 < len(p) <= maximum for p in parts)
    assert any(structure.encode() in p for p in parts)
    assert split(raw, len(raw), len(raw)) == [raw]


@pytest.mark.parametrize(
    "body,code",
    [
        ("````text\n" + "字" * 50 + "\n````\n", "document_split_unit_limit"),
        ("| a | b |\n" * 50, "document_split_unit_limit"),
        ("# Title\n\n[x](#Title)\n\n" * 10, "document_split_reference_scope"),
        ("[x]: relative.png\n\n" + "p\n\n" * 30, "document_split_reference_scope"),
        ("[x][label]\n\n" * 20, "document_split_reference_scope"),
        ("<div>\n\n" + "p\n\n" * 30, "document_split_reference_scope"),
        ("a\n\n" * 257, "document_split_part_limit"),
    ],
)
def test_unsafe_or_atomic_oversize_explicit_pause(body, code):
    with pytest.raises(SplitRequired, match=code):
        split(body.encode(), 3 if code.endswith("part_limit") else 70, 10000)
    with pytest.raises(SplitRequired, match="document_split_total_limit"):
        split(body.encode(), 10000, 1)


def test_reference_example_inside_fence_is_literal():
    raw = (
        "# start\n\nintro\n\n```md\n[x](#anchor)\n\n[y]: image.png\n```\n\n" + "p\n\n" * 40
    ).encode()
    assert b"".join(split(raw, 80, 1000)) == raw


def processed(config, owner, raw, *, maximum=130, references=None, mode="split"):
    cid, _, ids = owner
    vid = ids["personal"]
    db = database.connect(config.state_root)
    settings = source_processing_policy.save(config, None, WORKERS, None)
    value = {
        **settings["effective_policy"],
        "oversize_action": mode,
        "revision": settings["effective_policy"]["revision"] + 1,
    }
    settings = source_processing_policy.save(config, None, value, settings["configuration_sha256"])
    req = request(cid, vid, raw, "chapters.md", "process", settings["configuration_sha256"])
    req["overrides"] = {"markdown_bytes": maximum}
    req["references"] = references or {}
    item = upload(config, db, req, raw)
    rid = source_import_execution.prepare(config, db, cid, item["id"])
    db.close()
    with patch("fiber_bundle.document_worker.execute_document", side_effect=worker):
        source_import_execution.execute(config, rid)
    db = database.connect(config.state_root)
    meta, candidate = document_evidence.load(config, db, cid, rid)
    document_review.record(db, rid, candidate, assessment(candidate))
    return db, rid, item, meta, candidate


def chapters():
    return (
        "---\ntitle: 合成书\n---\n\n"
        + "".join(f"# 章节{i}\n\nchapter-token-{i} 压力为 {i}.4 MPa。\n\n" for i in range(8))
    ).encode()


def test_all_parts_lossless_stable_reviewed_and_indexed(http, config, store, owner):
    db, rid, item, meta, candidate = processed(config, owner, chapters())
    cid, _, ids = owner
    vid = ids["personal"]
    published = document_publication.publish_locked(config, db, rid)
    root = config.vault_path("test vault/temp_vault1")
    directory = root / published["relative_path"]
    manifest = json.loads((directory / document_parts.MANIFEST).read_bytes())
    body = document_publication.files(meta, candidate, document_review.latest(db, rid))["note.md"]
    assert b"".join((directory / p["path"]).read_bytes() for p in manifest["parts"]) == body
    assert manifest["rendered_sha256"] == objects.digest(body)
    assert manifest["source_sha256"] == objects.digest(chapters())
    assert len(manifest["parts"]) > 1
    assert all(p["byte_count"] <= 130 for p in manifest["parts"])
    ledger = json.loads(
        db.execute(
            "select manifest_json from document_publications where run_id=?", (rid,)
        ).fetchone()[0]
    )
    assert ledger[document_parts.MANIFEST]["sha256"] == objects.digest(
        (directory / document_parts.MANIFEST).read_bytes()
    )
    results = []
    for i in range(8):
        hit = search.vault_search(config, db, cid, f"chapter-token-{i}", [vid])["results"][0]
        assert hit["source_kind"] == "published_document" and hit["originals"]
        assert hit["source_version_id"] == meta["source_version_id"]
        assert hit["document_part"]["review_id"] == manifest["review_id"]
        assert hit["document_part"]["source_units"][0]["unit_id"] == "u1"
        assert hit["document_part"]["matches_reviewed_extraction"] is True
        assert hit["note_id"].startswith("note:")
        results.append(hit)
    note_search_build.build(config, cid, vid)
    for i, before in enumerate(results):
        assert search.vault_search(config, db, cid, f"chapter-token-{i}", [vid])["results"] == [
            before
        ]
    assert document_publication.publish_locked(config, db, rid)["historical"]
    assert store.read_asset(item["asset_id"], vault="personal") == chapters()
    assert db.execute("select count(*) from model_calls").fetchone()[0] == 0
    db.close()


def test_revisions_track_identity_and_no_recreation(http, config, store, owner):
    db, rid, _, _, _ = processed(config, owner, chapters())
    cid, headers, ids = owner
    vid = ids["personal"]
    directory = (
        config.vault_path("test vault/temp_vault1")
        / document_publication.publish_locked(config, db, rid)["relative_path"]
    )
    first = search.vault_search(config, db, cid, "chapter-token-7", [vid])["results"][0]
    tracked = note_identities.run(
        config, headers["Authorization"], vid, "track", first["note_path"], first["sha256"]
    )
    assert tracked["note_identity"] == first["note_id"]
    part = config.vault_path("test vault/temp_vault1") / first["note_path"]
    part.write_text(part.read_text() + "\nmanual-revision\n")
    note_search_build.build(config, cid, vid)
    revised = search.vault_search(config, db, cid, "manual-revision", [vid])["results"][0]
    assert revised["source_kind"] == "user_revision" and revised["note_id"] == first["note_id"]
    assert revised["document_part"]["matches_reviewed_extraction"] is False
    part.unlink()
    assert document_publication.publish_locked(config, db, rid)["historical"]
    assert not part.exists() and directory.exists()
    with pytest.raises(IntegrityError, match="incomplete"):
        note_inventory.read(config, db, auth.grant(db, cid, vid, "query"))
    db.close()


def test_manifest_tamper_and_unregistered_copy_not_authority(http, config, store, owner):
    db, rid, _, _, _ = processed(config, owner, chapters())
    cid, _, ids = owner
    root = config.vault_path("test vault/temp_vault1")
    directory = root / document_publication.publish_locked(config, db, rid)["relative_path"]
    marker = directory / document_parts.MANIFEST
    raw = marker.read_bytes()
    copied = root / "unregistered"
    copied.mkdir()
    (copied / document_parts.MANIFEST).write_bytes(raw)
    (copied / "part-0002.md").write_text("copied-unreviewed")
    assert (
        search.vault_search(config, db, cid, "copied-unreviewed", [ids["personal"]])["results"][0][
            "source_kind"
        ]
        == "historical_note"
    )
    marker.write_bytes(raw + b" ")
    with pytest.raises(IntegrityError, match="ledger"):
        search.vault_search(config, db, cid, "chapter-token-7", [ids["personal"]])
    db.close()


@pytest.mark.parametrize(
    "body,code",
    [
        (("# H\n\n" + "X" * 500).encode(), "document_split_unit_limit"),
        (("# H\n\n[a](#H)\n\n" * 40).encode(), "document_split_reference_scope"),
    ],
)
def test_pause_retains_original_candidate_review_no_partial_publish(
    http, config, store, owner, body, code
):
    db, rid, item, _, _ = processed(config, owner, body)
    response = http.post(f"/admin/documents/{rid}/publish")
    assert response.status_code == 409 and response.json() == {"error": code}
    assert db.execute("select count(*) from document_publications").fetchone()[0] == 0
    assert (
        db.execute("select error_code from document_tasks where run_id=?", (rid,)).fetchone()[0]
        == code
    )
    assert document_evidence.load(config, db, owner[0], rid)[1]["complete"]
    assert document_review.latest(db, rid) is not None
    from fiber_bundle import source_imports

    projection = source_imports.projection(db, source_imports.get(config, db, owner[0], item["id"]))
    assert projection["state"] == "needs_split" and projection["publication_pause"] == code
    assert store.read_asset(item["asset_id"], vault="personal") == body
    assert not list(config.vault_path("test vault/temp_vault1").glob("Document-*"))
    db.close()


def test_split_rename_crash_hidden_until_explicit_reconcile(http, config, store, owner):
    db, rid, _, _, _ = processed(config, owner, chapters())
    rename = publication_files.rename_exclusive

    def crash(*args):
        rename(*args)
        raise RuntimeError("synthetic post-rename interruption")

    with patch("fiber_bundle.publication_files.rename_exclusive", side_effect=crash):
        with pytest.raises(RuntimeError):
            document_publication.publish_locked(config, db, rid)
    assert (
        search.vault_search(config, db, owner[0], "chapter-token-7", [owner[2]["personal"]])[
            "results"
        ]
        == []
    )
    result = document_publication.publish_locked(config, db, rid)
    assert result["state"] == "published"
    assert search.vault_search(config, db, owner[0], "chapter-token-7", [owner[2]["personal"]])[
        "results"
    ]
    db.close()


def test_catalog_is_complete_navigation_and_not_primary_evidence(http, config, store, owner):
    db, rid, _, _, _ = processed(config, owner, chapters())
    result = document_publication.publish_locked(config, db, rid)
    root = config.vault_path("test vault/temp_vault1")
    marker = json.loads((root / result["relative_path"] / document_parts.MANIFEST).read_text())
    catalog = (root / result["relative_path"] / "index.md").read_text()
    assert all(f"]({p['path']})" in catalog for p in marker["parts"])
    hit = search.vault_search(config, db, owner[0], "文档分片目录", [owner[2]["personal"]])[
        "results"
    ][0]
    assert hit["source_kind"] == "agent_analysis" and hit["independent_evidence"] is False
    assert hit["originals"] == [] and hit["source_version_id"] is None
    assert hit["document_part"]["role"] == "catalog"
    assert hit["document_part"]["matches_reviewed_extraction"] is False
    from fiber_bundle.knowledge_graph.contracts import Source

    Source(
        hit["note_id"],
        hit["sha256"],
        objects.digest(catalog.encode()),
        hit["title"],
        catalog,
        hit["source_kind"],
        hit["source_version_id"],
    ).validate()
    db.close()


def test_part_original_visibility_and_current_query_scope(http, config, store, owner):
    db, rid, _, _, _ = processed(config, owner, chapters())
    document_publication.publish_locked(config, db, rid)
    cid, _, ids = owner
    vid = ids["personal"]
    note_search_build.build(config, cid, vid)
    auth.set_grant(db, cid, vid, query=True, original=False, add=False)
    hit = search.vault_search(config, db, cid, "chapter-token-7", [vid])["results"][0]
    assert hit["source_kind"] == "published_document" and hit["originals"] == []
    auth.set_grant(db, cid, vid, query=False, original=False, add=False)
    with pytest.raises(auth.Denied):
        search.vault_search(config, db, cid, "chapter-token-7", [vid])
    db.close()


def test_attachment_links_are_relative_to_every_sibling_part(http, config, store, owner):
    cid, _, ids = owner
    db = database.connect(config.state_root)
    attachment = upload(
        config,
        db,
        request(cid, ids["personal"], b"synthetic-image", "image.png"),
        b"synthetic-image",
    )
    db.close()
    raw = chapters() + b"\n![image](assets/image.png)\n\n"
    db, rid, _, _, _ = processed(
        config, owner, raw, maximum=170, references={"assets/image.png": attachment["id"]}
    )
    result = document_publication.publish_locked(config, db, rid)
    directory = config.vault_path("test vault/temp_vault1") / result["relative_path"]
    manifest = json.loads((directory / document_parts.MANIFEST).read_bytes())
    link_part = next(
        directory / p["path"]
        for p in manifest["parts"]
        if b"![image]" in (directory / p["path"]).read_bytes()
    )
    import re

    reference = re.search(r"!\[image\]\(([^)]+)\)", link_part.read_text())[1]
    assert (link_part.parent / reference).read_bytes() == b"synthetic-image"
    assert store.read_asset(attachment["asset_id"], vault="personal") == b"synthetic-image"
    db.close()


def test_native_page_parts_record_exact_locator_and_rendered_spans():
    from fiber_bundle.document_rendering import render

    metadata = dict(
        run_id="synthetic-run",
        source_version_id="synthetic-version",
        asset_id="synthetic-asset",
        source_sha256="a" * 64,
        result_sha256="b" * 64,
        vault_id="synthetic-vault",
    )
    candidate = dict(
        schema=1,
        pages=[
            dict(physical_page=i, pdf_page_label=str(i), text=f"page-token-{i} " + "x" * 330)
            for i in range(1, 5)
        ],
    )
    review = {"id": "synthetic-review", "assessment_json": "{}"}
    files, spans = render(metadata, candidate, review)
    intended = document_parts.publish_files(files, metadata, review["id"], spans, 900, 10000)
    manifest = json.loads(intended[document_parts.MANIFEST])
    for i in range(1, 5):
        entry = next(
            e for e in manifest["parts"] if f"page-token-{i}".encode() in intended[e["path"]]
        )
        unit = next(u for u in entry["source_units"] if u["unit_id"] == f"P{i}")
        assert unit["locator"] == {"physical_page": i, "pdf_page_label": str(i)}
        assert b"page-token-" in files["note.md"][unit["rendered_start"] : unit["rendered_end"]]
    assert b"".join(intended[e["path"]] for e in manifest["parts"]) == files["note.md"]


@pytest.mark.parametrize("mode", ["pause", "split"])
def test_old_and_new_tasks_keep_frozen_oversize_mode(http, config, store, owner, mode):
    db, rid, _, _, _ = processed(config, owner, chapters(), mode=mode)
    settings = source_processing_policy.inspect(config, None)
    changed = {
        **settings["effective_policy"],
        "oversize_action": "pause" if mode == "split" else "split",
        "revision": settings["effective_policy"]["revision"] + 1,
    }
    source_processing_policy.save(config, None, changed, settings["configuration_sha256"])
    if mode == "pause":
        with pytest.raises(InvalidInput, match="processing_markdown_file_limit"):
            document_publication.publish_locked(config, db, rid)
        assert db.execute("select count(*) from document_publications").fetchone()[0] == 0
    else:
        assert document_publication.publish_locked(config, db, rid)["state"] == "published"
    db.close()


def test_backup_restore_keeps_parts_review_ids_and_forces_index_rebuild(
    http, config, store, owner, tmp_path
):
    from fiber_bundle import backup_bundle, backup_restore, note_search_cache
    from fiber_bundle.config import load_config

    db, rid, _, _, _ = processed(config, owner, chapters())
    document_publication.publish_locked(config, db, rid)
    cid, _, ids = owner
    vid = ids["personal"]
    first = search.vault_search(config, db, cid, "chapter-token-7", [vid])["results"][0]
    note_search_build.build(config, cid, vid)
    review = dict(document_review.latest(db, rid))
    db.close()
    backup_bundle.create(config, tmp_path / "backup", "synthetic-backup-password")
    backup_restore.restore(tmp_path / "backup", tmp_path / "restored", "synthetic-backup-password")
    target = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    backup_restore.activate(target, source_stopped=True)
    db = database.connect(target.state_root)
    assert db.execute("select active from clients where id=?", (cid,)).fetchone()[0] == 0
    # Explicit synthetic owner reactivation; imported access is never automatically enabled.
    db.execute("update clients set active=1 where id=?", (cid,))
    auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    assert dict(document_review.latest(db, rid)) == review
    assert (
        note_search_cache.status(target, db, auth.grant(db, cid, vid, "query"))["state"] == "stale"
    )
    note_search_build.build(target, cid, vid)
    hit = search.vault_search(target, db, cid, "chapter-token-7", [vid])["results"][0]
    assert hit == first
    assert db.execute("select count(*) from model_calls").fetchone()[0] == 0
    db.close()
