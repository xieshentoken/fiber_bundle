import base64
from unittest.mock import patch

import pytest
from test_api import add_client, permissions, vault_ids

from fiber_bundle import database, objects, session_search
from fiber_bundle.store import Store


@pytest.fixture
def owner(http):
    cid, headers = add_client(http)
    ids = vault_ids(http)
    permissions(http, cid, ids["personal"], query=True, add=True)
    return cid, headers, ids


def intake(
    http,
    owner,
    session="current",
    event="one",
    body=b"100 psi session evidence",
    media="text/plain",
    role="body",
    vault="personal",
    source=None,
):
    _, headers, ids = owner
    asset = {
        "role": role,
        "media_type": media,
        "original_name": "material.txt",
        "data": base64.b64encode(body).decode() if body is not None else None,
    }
    if body is None:
        asset.update(expected_sha256="a" * 64, expected_size=40)
    response = http.post(
        "/v1/receipts",
        headers=headers,
        json={
            "vault_id": ids[vault],
            "event_id": event,
            "source_key": source or event,
            "session_id": session,
            "raw_event": base64.b64encode(b"RAW-EVENT-SECRET").decode(),
            "assets": [asset],
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def find(http, owner, query="100 psi", session="current", **extra):
    payload = {"query": query, **extra}
    if session is not None:
        payload["session_id"] = session
    return http.post("/v1/search", headers=owner[1], json=payload)


def test_session_default_is_separate_from_default_vault_switch(http, owner, config):
    receipt = intake(http, owner)
    result = find(http, owner).json()
    assert result["searched_vault_ids"] == [] and result["searched_session_id"] == "current"
    hit = result["results"][0]
    assert hit["source_kind"] == "session_material" and hit["note_path"] is None
    assert (
        hit["receipt_id"] == receipt["job_id"]
        and hit["source_version_id"] == receipt["source_version_id"]
    )
    assert hit["snippet"] == "100 psi session evidence" and hit["line"] == 1
    assert hit["originals"] == []
    assert find(http, owner, session=None).json()["results"] == []
    assert find(http, owner, session="new-topic").json()["results"] == []
    assert find(http, owner, query="RAW-EVENT-SECRET").json()["results"] == []
    assert all(not list((config.project_root / path).iterdir()) for _, path in config.vaults)


def test_same_session_name_other_clients_and_other_sessions_isolated(http, owner):
    intake(http, owner, body=b"private-one")
    cid, headers = add_client(http, "other")
    permissions(http, cid, owner[2]["personal"], query=True, add=True)
    other = (cid, headers, owner[2])
    intake(http, other, body=b"private-two")
    assert find(http, other, query="private-one").json()["results"] == []
    assert find(http, owner, query="private-two").json()["results"] == []
    assert len(find(http, other, query="private-two").json()["results"]) == 1
    intake(http, owner, session="past", event="past", body=b"old-topic")
    assert find(http, owner, query="old-topic").json()["results"] == []
    assert len(find(http, owner, query="old-topic", session="past").json()["results"]) == 1


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
def test_permission_matrix(http, owner, query, original, add):
    intake(http, owner)
    permissions(http, owner[0], owner[2]["personal"], query=query, original=original, add=add)
    hits = find(http, owner).json()["results"]
    assert bool(hits) == query
    if hits:
        assert bool(hits[0]["originals"]) == original


def test_explicit_scope_restricts_session_too(http, owner):
    permissions(http, owner[0], owner[2]["supplier"], query=True, add=True)
    intake(http, owner, vault="personal")
    intake(http, owner, vault="supplier", event="two")
    assert len(find(http, owner).json()["results"]) == 2
    assert find(http, owner, vault_ids=[]).json()["results"] == []
    result = find(http, owner, vault_ids=[owner[2]["supplier"]]).json()["results"]
    assert len(result) == 1 and result[0]["vault_id"] == owner[2]["supplier"]
    permissions(http, owner[0], owner[2]["supplier"], query=False)
    with patch.object(objects, "read_object", side_effect=AssertionError("read before auth")):
        assert find(http, owner, vault_ids=list(owner[2].values())).status_code == 404


def test_clear_session_reference_prevents_retrieval_preserves_original(http, owner, config):
    receipt = intake(http, owner)
    hit = find(http, owner).json()["results"][0]
    permissions(http, owner[0], owner[2]["personal"], query=True, original=True)
    url = find(http, owner).json()["results"][0]["originals"][0]["url"]
    Store(config).clear_session_refs("client:" + owner[0], "current")
    assert find(http, owner).json()["results"] == []
    assert http.get(url, headers=owner[1]).content == b"100 psi session evidence"
    assert Store(config).receipt(receipt["job_id"]).source_version_id == hit["source_version_id"]


def test_deduplicated_version_requires_own_matching_session_receipt(http, owner, config):
    one = intake(http, owner, session="first", event="one", source="same")
    two = intake(http, owner, session="second", event="two", source="same")
    assert one["source_version_id"] == two["source_version_id"]
    assert find(http, owner, session="first").json()["results"][0]["receipt_id"] == one["job_id"]
    assert find(http, owner, session="second").json()["results"][0]["receipt_id"] == two["job_id"]
    assert len(find(http, owner, session="second").json()["results"]) == 1
    Store(config).clear_session_refs("client:" + owner[0], "first")
    assert find(http, owner, session="first").json()["results"] == []
    assert len(find(http, owner, session="second").json()["results"]) == 1


@pytest.mark.parametrize(
    "media,role",
    [
        ("text/uri-list", "body"),
        ("text/html", "body"),
        ("application/pdf", "body"),
        ("text/plain", "attachment"),
    ],
)
def test_unsupported_assets_not_indexed(http, owner, media, role):
    intake(http, owner, media=media, role=role)
    assert find(http, owner).json()["results"] == []


def test_incomplete_receipt_excluded(http, owner):
    receipt = intake(http, owner, body=None)
    assert receipt["receipt_state"] == "incomplete"
    assert find(http, owner).json()["results"] == []


def test_manifest_and_object_corruption_fail_closed(http, owner, config):
    receipt = intake(http, owner)
    db = database.connect(config.state_root)
    db.execute("UPDATE receipts SET input_digest=? WHERE job_id=?", ("0" * 64, receipt["job_id"]))
    db.close()
    response = find(http, owner)
    assert response.status_code == 503 and "session evidence" not in response.text


def test_object_corruption_and_utf8_failure(http, owner, config):
    intake(http, owner, body=b"\xff")
    assert find(http, owner).status_code == 503


def test_combined_results_numbering_and_limit(http, owner, config):
    intake(http, owner)
    path = config.project_root / "test vault/temp_vault1/note.md"
    path.write_text("# manual\n100 psi note evidence")
    http.patch("/admin/vaults/" + owner[2]["personal"], json={"default_query": True})
    hits = find(http, owner).json()["results"]
    assert [h["source_kind"] for h in hits] == ["session_material", "historical_note"]
    assert [h["citation"] for h in hits] == ["[1]", "[2]"]
    assert len(find(http, owner, limit=1).json()["results"]) == 1
    assert path.read_text() == "# manual\n100 psi note evidence"


def test_session_limits_and_invalid_input(http, owner):
    intake(http, owner)
    assert find(http, owner, session="").status_code == 422
    assert find(http, owner, session="bad\x00id").status_code == 422
    assert find(http, owner, session_scope="client:any").status_code == 422
    with patch.object(session_search, "MAX_BODY_BYTES", 4):
        assert find(http, owner).status_code == 503
    with patch.object(session_search, "MAX_RECEIPTS", 0):
        assert find(http, owner).status_code == 503


def test_original_links_are_independently_authorized_and_revocable(http, owner):
    intake(http, owner)
    permissions(http, owner[0], owner[2]["personal"], query=True, original=True)
    hit = find(http, owner).json()["results"][0]
    url = hit["originals"][0]["url"]
    assert http.get(url, headers=owner[1]).content == b"100 psi session evidence"
    permissions(http, owner[0], owner[2]["personal"], query=True, original=False)
    assert http.get(url, headers=owner[1]).status_code == 404
    assert find(http, owner).json()["results"][0]["originals"] == []


def test_object_hash_mutation_never_returns_snippet(http, owner, config):
    receipt = intake(http, owner)
    db = database.connect(config.state_root)
    path = db.execute(
        "SELECT a.relative_path FROM assets a JOIN version_assets va ON va.asset_id=a.id "
        "WHERE va.version_id=?",
        (receipt["source_version_id"],),
    ).fetchone()[0]
    db.close()
    original = config.state_root / path
    original.write_bytes(b"X" * original.stat().st_size)
    response = find(http, owner)
    assert response.status_code == 503 and "100 psi" not in response.text


def test_session_reference_removed_before_delivery_refused(http, owner, config):
    intake(http, owner)
    read = session_search.body
    count = 0

    def removed(*args, **kwargs):
        nonlocal count
        data = read(*args, **kwargs)
        count += 1
        if count == 1:
            db = database.connect(config.state_root)
            db.execute("DELETE FROM session_refs")
            db.close()
        return data

    with patch.object(session_search, "body", side_effect=removed):
        assert find(http, owner).status_code == 503


def test_revoked_query_before_delivery_refused(http, owner, config):
    intake(http, owner)
    read = session_search.body

    def revoked(*args, **kwargs):
        data = read(*args, **kwargs)
        db = database.connect(config.state_root)
        db.execute("UPDATE client_vault_grants SET can_query=0")
        db.close()
        return data

    with patch.object(session_search, "body", side_effect=revoked):
        assert find(http, owner).status_code == 404


def test_markdown_body_is_searchable_but_not_verified_knowledge(http, owner):
    intake(http, owner, body="# 计算草稿\n冷板压力 100 psi。".encode(), media="text/markdown")
    hit = find(http, owner).json()["results"][0]
    assert hit["line"] == 2 and hit["source_kind"] == "session_material"
    assert "不代表知识已核验" in hit["source_label"]
