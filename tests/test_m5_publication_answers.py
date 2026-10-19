"""M4 publication through M5 answers and original recovery, in both synthetic Vaults."""

import hashlib

import pytest
from test_answer_submissions import enabled as enabled
from test_answer_tasks import invoke, start
from test_api import add_client, permissions
from test_article_publication import completed
from test_articles import X_URL, current_html
from test_execution import policy as policy
from test_session_search import intake
from test_session_search import owner as owner

from fiber_bundle import article_publication, database, execution_ledger, note_identities


@pytest.fixture(params=["personal", "supplier"])
def published(request, config, policy, http, owner):
    vault = request.param
    vid = owner[2][vault]
    permissions(http, owner[0], vid, add=True)
    receipt = intake(http, owner, vault=vault, body=X_URL.encode(), media="text/uri-list")
    rid = completed(config, policy, (owner[0], vid, receipt))
    result = article_publication.publish(config, owner[0], rid)
    root = config.project_root / dict(config.vaults)[vault]
    note = root / result["note_path"]
    permissions(http, owner[0], vid, query=True)
    return vid, note, result


def answered(enabled, owner, published, config, policy):
    vid, note, result = published
    created = enabled.post(
        "/v1/note-answer-tasks",
        headers=owner[1],
        json={
            "request_id": "publication-answer",
            "vault_id": vid,
            "question": "正文提供了哪些合成证据？",
            "retrieval_query": "合成",
        },
    )
    assert created.status_code == 202
    rid = created.json()["run_id"]
    start(config, policy, rid)
    invoke(config, policy, rid)
    db = database.connect(config.state_root)
    execution_ledger.transition(db, rid, "succeeded")
    db.close()
    url = "/v1/answers/" + rid
    response = enabled.get(url, headers=owner[1])
    assert response.status_code == 200
    answer = response.json()
    assert answer["searched_vault_ids"] == [vid]
    assert len(answer["sources"]) == 1
    source = answer["sources"][0]
    assert source["source_kind"] == "published_article"
    assert source["source_version_id"] == result["source_version_id"]
    assert source["sha256"] == hashlib.sha256(note.read_bytes()).hexdigest()
    assert source["snippet"] in note.read_text().splitlines()[source["line"] - 1]
    assert source["source_url"] == X_URL
    assert answer["quotations"] == [{"citation": "[1]", "quote": source["snippet"]}]
    assert source["originals"] == []
    return url


def test_tracking_and_single_filename_rename_invalidate_old_answer(
    enabled, owner, published, config, policy, http
):
    vid, note, publication = published
    url = answered(enabled, owner, published, config, policy)
    permissions(http, owner[0], vid, query=True, add=True)
    root = (
        config.project_root
        / "test vault"
        / ("temp_vault1" if vid == owner[2]["personal"] else "temp_vault2")
    )
    tracked = note_identities.run(
        config,
        owner[1]["Authorization"],
        vid,
        "track",
        note.relative_to(root).as_posix(),
        hashlib.sha256(note.read_bytes()).hexdigest(),
    )
    assert enabled.get(url, headers=owner[1]).status_code == 503
    note.rename(note.with_name("user-renamed.md"))
    fresh = enabled.post(
        "/v1/answers",
        headers=owner[1],
        json={"question": "合成资料是什么？", "retrieval_query": "合成", "vault_ids": [vid]},
    )
    assert fresh.status_code == 200
    source = fresh.json()["sources"][0]
    assert source["note_id"] == tracked["note_identity"]
    assert source["source_version_id"] == publication["source_version_id"]
    assert source["note_path"].endswith("/user-renamed.md")
    assert source["source_kind"] == "published_article"


def test_moved_notes_invalidate_old_answer_but_new_query_keeps_source(
    enabled, owner, published, config, policy
):
    vid, note, result = published
    url = answered(enabled, owner, published, config, policy)
    old_source = enabled.get(url, headers=owner[1]).json()["sources"][0]
    target = note.parent.parent / "未分类/新位置"
    target.parent.mkdir()
    note.parent.rename(target)
    note = target / "note.md"
    assert enabled.get(url, headers=owner[1]).status_code == 503
    fresh = enabled.post(
        "/v1/answers",
        headers=owner[1],
        json={"question": "合成资料是什么？", "retrieval_query": "合成", "vault_ids": [vid]},
    )
    assert fresh.status_code == 200
    source = fresh.json()["sources"][0]
    assert source["note_path"] != old_source["note_path"]
    assert source["note_path"].endswith("未分类/新位置/note.md")
    assert source["sha256"] == old_source["sha256"]
    assert source["source_version_id"] == result["source_version_id"]
    assert source["source_kind"] == "published_article"
    assert fresh.json()["quotations"][0]["quote"] in note.read_text()


def test_published_answer_to_original_authority_and_read_only_files(
    enabled, owner, published, config, policy, http
):
    vid, note, _ = published
    paths = [p for p in note.parent.rglob("*") if p.is_file()]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in paths}
    url = answered(enabled, owner, published, config, policy)
    permissions(http, owner[0], vid, query=True, original=True)
    data = enabled.get(url, headers=owner[1]).json()
    original = next(x for x in data["sources"][0]["originals"] if x["name"] == "source.html")
    raw = enabled.get(original["url"], headers=owner[1])
    assert raw.status_code == 200 and raw.content == current_html()
    _, other = add_client(http, "unrelated-reader")
    assert enabled.get(url, headers=other).status_code == 404
    assert enabled.get(original["url"], headers=other).status_code == 404
    permissions(http, owner[0], vid, query=True)
    assert enabled.get(original["url"], headers=owner[1]).status_code == 404
    assert enabled.get(url, headers=owner[1]).json()["sources"][0]["originals"] == []
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in paths}


def test_user_revision_and_deletion_invalidate_answer_preserve_original(
    enabled, owner, published, config, policy, http
):
    vid, note, _ = published
    url = answered(enabled, owner, published, config, policy)
    permissions(http, owner[0], vid, query=True, original=True)
    old = enabled.get(url, headers=owner[1]).json()["sources"][0]
    original_url = next(x["url"] for x in old["originals"] if x["name"] == "source.html")
    note.write_text("# 人工修订\n合成实验压力 90 psi，需核对。\n")
    changed = enabled.get(url, headers=owner[1])
    assert changed.status_code == 503 and "quotations" not in changed.json()
    query = {"question": "有什么合成证据？", "retrieval_query": "合成", "vault_ids": [vid]}
    current = enabled.post("/v1/answers", headers=owner[1], json=query)
    assert current.status_code == 200
    source = current.json()["sources"][0]
    assert source["source_kind"] == "user_revision"
    assert source["source_version_id"] == old["source_version_id"]
    assert source["sha256"] != old["sha256"]
    assert any("用户修订" in caution for caution in current.json()["cautions"])
    note.unlink()
    empty = enabled.post("/v1/answers", headers=owner[1], json=query)
    assert empty.status_code == 200 and empty.json()["status"] == "insufficient_evidence"
    assert enabled.get(url, headers=owner[1]).status_code == 503
    assert enabled.get(original_url, headers=owner[1]).content == current_html()
    assert not note.exists()
