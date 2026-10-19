import hashlib
import os
from unittest.mock import patch

import pytest
from test_api import add_client, permissions, vault_ids
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy

from fiber_bundle import auth, database, note_index, search
from fiber_bundle.config import IntegrityError


@pytest.fixture
def searchable(http, config):
    cid, headers = add_client(http)
    ids = vault_ids(http)
    permissions(http, cid, ids["personal"], query=True)
    root = config.project_root / "test vault/temp_vault1"
    (root / "cooling.md").write_text(
        "# 液冷实验\n6 kW MC-IHS 压力为 100 psi。\n型号 AB-12 合格。\n"
    )
    (root / "other.md").write_text("# 冲突记录\n液冷实验压力为 80 psi。\n型号 AB-123 待核实。\n")
    return cid, headers, ids, root


def retrieve(http, headers, query, ids=None, **extra):
    data = {"query": query, **extra}
    if ids is not None:
        data["vault_ids"] = ids
    return http.post("/v1/search", headers=headers, json=data)


def test_chinese_exact_codes_and_no_evidence(http, searchable):
    _, headers, ids, _ = searchable
    response = retrieve(http, headers, "液冷", [ids["personal"]])
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2
    assert response.headers["cache-control"] == "no-store"
    result = retrieve(http, headers, "AB-12", [ids["personal"]]).json()["results"]
    assert len(result) == 1 and result[0]["note_path"] == "cooling.md"
    assert result[0]["line"] == 3 and result[0]["snippet"] == "型号 AB-12 合格。"
    assert result[0]["source_kind"] == "historical_note"
    assert result[0]["source_label"] == "历史笔记，来源未核实"
    assert result[0]["citation"] == "[1]" and result[0]["originals"] == []
    assert retrieve(http, headers, "unknown", [ids["personal"]]).json()["status"] == "no_evidence"
    assert len(retrieve(http, headers, "ＭＣ－ＩＨＳ", [ids["personal"]]).json()["results"]) == 1


def test_default_scope_separate_from_authorization_and_explicit_target(http, searchable):
    cid, headers, ids, root = searchable
    assert retrieve(http, headers, "液冷").json()["searched_vault_ids"] == []
    assert (
        http.patch("/admin/vaults/" + ids["personal"], json={"default_query": True}).status_code
        == 200
    )
    assert len(retrieve(http, headers, "液冷").json()["results"]) == 2
    # Explicit empty scope never falls back to defaults.
    assert retrieve(http, headers, "液冷", []).json()["results"] == []
    denied = retrieve(http, headers, "液冷", [ids["personal"], ids["supplier"]])
    assert denied.status_code == 404 and "cooling" not in denied.text
    permissions(http, cid, ids["personal"], query=False, original=True, add=True)
    assert retrieve(http, headers, "液冷").json()["results"] == []
    assert retrieve(http, headers, "液冷", [ids["personal"]]).status_code == 404


def test_other_client_cannot_query_or_reuse_results(http, searchable):
    _, headers, ids, _ = searchable
    assert retrieve(http, headers, "液冷", [ids["personal"]]).json()["results"]
    _, other = add_client(http, "other")
    assert retrieve(http, other, "液冷", [ids["personal"]]).status_code == 404
    assert retrieve(http, other, "液冷").json()["results"] == []
    assert retrieve(http, {}, "液冷", [ids["personal"]]).status_code == 401


def test_snapshot_rebuild_fresh_edits_deletion_and_read_only_bytes(http, searchable):
    _, headers, ids, root = searchable
    path = root / "cooling.md"
    before = {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in root.iterdir()
    }
    a = retrieve(http, headers, "液冷", [ids["personal"]]).json()
    b = retrieve(http, headers, "液冷", [ids["personal"]]).json()
    assert a == b
    assert before == {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in root.iterdir()
    }
    path.write_text("# 用户修改\n新证据 XY-78\n")
    assert retrieve(http, headers, "AB-12", [ids["personal"]]).json()["results"] == []
    assert (
        retrieve(http, headers, "XY-78", [ids["personal"]]).json()["results"][0]["sha256"]
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    path.unlink()
    assert retrieve(http, headers, "XY-78", [ids["personal"]]).json()["results"] == []
    assert not path.exists()


def test_hidden_partial_managed_symlinks_and_non_markdown_excluded(http, searchable, tmp_path):
    _, headers, ids, root = searchable
    for folder in [".fiber-stage-synthetic", ".obsidian", "article-unknown", "moved-partial"]:
        child = root / folder
        child.mkdir()
        (child / "hidden.md").write_text("excluded-sentinel")
    (root / "moved-partial" / ".fiber-publication.json").write_text("{}")
    (root / "article-unknown" / ".fiber-publication.json").write_text("{}")
    outside = tmp_path / "outside.md"
    outside.write_text("excluded-sentinel")
    (root / "link.md").symlink_to(outside)
    (root / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    (root / "raw.html").write_text("excluded-sentinel")
    assert retrieve(http, headers, "excluded-sentinel", [ids["personal"]]).json()["results"] == []


@pytest.mark.parametrize("query", ["", " ", "x" * 201, "a b c d e f g h i", "\x00", "***"])
def test_invalid_queries(http, searchable, query):
    _, headers, ids, _ = searchable
    assert retrieve(http, headers, query, [ids["personal"]]).status_code == 422


def test_duplicate_scope_limit_and_no_query_language(http, searchable):
    _, headers, ids, _ = searchable
    assert retrieve(http, headers, "液冷", [ids["personal"], ids["personal"]]).status_code == 422
    assert retrieve(http, headers, "液冷", [ids["personal"]], limit=0).status_code == 422
    assert retrieve(http, headers, "AB-12 OR AB-123", [ids["personal"]]).json()["results"] == []
    assert len(retrieve(http, headers, "液冷", [ids["personal"]], limit=1).json()["results"]) == 1


def test_hardlink_and_oversize_refused_without_partial_results(http, searchable):
    _, headers, ids, root = searchable
    os.link(root / "cooling.md", root / "hard.md")
    assert retrieve(http, headers, "液冷", [ids["personal"]]).status_code == 503
    (root / "hard.md").unlink()
    (root / "big.md").write_bytes(b"x" * (1024 * 1024 + 1))
    response = retrieve(http, headers, "液冷", [ids["personal"]])
    assert response.status_code == 503 and "cooling" not in response.text


def test_invalid_utf8_refused(http, searchable):
    _, headers, ids, root = searchable
    (root / "invalid.md").write_bytes(b"\xff")
    assert retrieve(http, headers, "液冷", [ids["personal"]]).status_code == 503


def test_changed_evidence_before_delivery_refused(http, searchable):
    _, headers, ids, root = searchable
    read = note_index.read_note
    count = 0

    def changed(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 3:
            (root / "cooling.md").write_text("replaced evidence")
        return read(*args, **kwargs)

    with patch.object(note_index, "read_note", side_effect=changed):
        assert retrieve(http, headers, "AB-12", [ids["personal"]]).status_code == 503


def test_scan_limits_and_plain_empty_notes(tmp_path):
    (tmp_path / "blank.md").write_bytes(b"")
    assert note_index.snapshot(tmp_path, set(), set())[0].text == ""
    with pytest.raises(IntegrityError):
        note_index.snapshot(tmp_path, set(), set(), note_index.Limits(entries=0))
    (tmp_path / "blank.md").write_text("123456")
    with pytest.raises(IntegrityError):
        note_index.snapshot(tmp_path, set(), set(), note_index.Limits(total_bytes=2))


def test_published_source_revisions_and_original_permission(config, policy, article_scope):
    from test_article_publication import completed

    from fiber_bundle.article_publication import publish

    rid = completed(config, policy, article_scope)
    result = publish(config, article_scope[0], rid)
    db = database.connect(config.state_root)
    cid, vid, _ = article_scope
    auth.set_grant(db, cid, vid, query=True, original=False, add=True)
    hits = search.search(config, db, cid, "Synthetic", [vid])["results"]
    assert hits[0]["source_kind"] == "published_article" and hits[0]["originals"] == []
    auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    hit = search.search(config, db, cid, "Synthetic", [vid])["results"][0]
    assert hit["source_version_id"] == result["source_version_id"]
    assert any(a["name"] == "source.html" for a in hit["originals"])
    note = config.project_root / "test vault/temp_vault1" / result["note_path"]
    note.write_text("# Synthetic\n人工修訂不同於原文。\n")
    assert (
        search.search(config, db, cid, "人工", [vid])["results"][0]["source_kind"]
        == "user_revision"
    )
    db.execute("UPDATE article_publications SET state='ready'")
    assert search.search(config, db, cid, "人工", [vid])["results"] == []
    db.close()


@pytest.mark.parametrize(
    "query,original,add",
    [
        (False, False, False),
        (False, False, True),
        (False, True, False),
        (False, True, True),
        (True, False, False),
        (True, False, True),
        (True, True, False),
        (True, True, True),
    ],
)
def test_query_permission_matrix(http, searchable, query, original, add):
    cid, headers, ids, _ = searchable
    permissions(http, cid, ids["personal"], query=query, original=original, add=add)
    response = retrieve(http, headers, "液冷", [ids["personal"]])
    assert response.status_code == (200 if query else 404)
    if query:
        assert len(response.json()["results"]) == 2


def test_denied_scope_never_reads_any_vault(http, searchable):
    _, headers, ids, _ = searchable
    with patch.object(note_index, "snapshot", side_effect=AssertionError("must authorize first")):
        assert (
            retrieve(http, headers, "液冷", [ids["personal"], ids["supplier"]]).status_code == 404
        )


def test_conflicting_sources_are_both_returned_without_claim_resolution(http, searchable):
    _, headers, ids, _ = searchable
    results = retrieve(http, headers, "压力", [ids["personal"]]).json()["results"]
    assert {r["snippet"] for r in results} == {
        "6 kW MC-IHS 压力为 100 psi。",
        "液冷实验压力为 80 psi。",
    }
    assert len({r["citation"] for r in results}) == 2


def test_nested_notes_literal_punctuation_and_long_line_window(http, searchable):
    _, headers, ids, root = searchable
    sub = root / "未分类"
    sub.mkdir()
    line = "背景。" * 300 + " 芯片 AA.12 温度 62 °C。"
    (sub / "records.MD").write_text("# 条目\n" + line)
    results = retrieve(http, headers, "AA.12", [ids["personal"]]).json()["results"]
    assert len(results) == 1 and results[0]["line"] == 2
    assert "AA.12" in results[0]["snippet"] and len(results[0]["snippet"]) <= 400
    assert retrieve(http, headers, "AAx12", [ids["personal"]]).json()["results"] == []


def test_unauthorized_default_vault_is_not_scanned(http, searchable, config):
    _, headers, ids, _ = searchable
    other = config.project_root / "test vault/temp_vault2"
    (other / "bad.md").write_bytes(b"\xff")
    assert (
        http.patch("/admin/vaults/" + ids["supplier"], json={"default_query": True}).status_code
        == 200
    )
    assert (
        http.patch("/admin/vaults/" + ids["personal"], json={"default_query": True}).status_code
        == 200
    )
    result = retrieve(http, headers, "液冷").json()
    assert result["searched_vault_ids"] == [ids["personal"]]
    assert len(result["results"]) == 2


def test_revoked_client_and_mid_query_revocation(http, searchable, config):
    cid, headers, ids, root = searchable
    snapshot = note_index.snapshot

    def revoke(*args, **kwargs):
        result = snapshot(*args, **kwargs)
        db = database.connect(config.state_root)
        db.execute("UPDATE clients SET active=0 WHERE id=?", (cid,))
        db.close()
        return result

    with patch.object(note_index, "snapshot", side_effect=revoke):
        assert retrieve(http, headers, "液冷", [ids["personal"]]).status_code == 404
    assert retrieve(http, headers, "液冷").status_code == 401


def test_ordinary_article_named_directory_is_not_a_reserved_bundle(http, searchable):
    _, headers, ids, root = searchable
    directory = root / "article-manual-notes"
    directory.mkdir()
    (directory / "manual.md").write_text("手工目录检索样本")
    hits = retrieve(http, headers, "手工目录", [ids["personal"]]).json()["results"]
    assert len(hits) == 1 and hits[0]["source_kind"] == "historical_note"
