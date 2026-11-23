"""Real admin-session transport for read-only, client-scoped note lookup."""

from uuid import uuid4

import pytest
from test_api import add_client, permissions, vault_ids

from fiber_bundle import database


@pytest.fixture
def notes(http, config):
    client, _ = add_client(http)
    vaults = vault_ids(http)
    permissions(http, client, vaults["personal"], query=True)
    root = config.project_root / "test vault/temp_vault1"
    (root / "cooling.md").write_text("# Synthetic cooling\nPressure sentinel 100 psi.\n")
    other = config.project_root / "test vault/temp_vault2"
    (other / "private.md").write_text("# Private supplier\nPressure sentinel secret.\n")
    return client, vaults, root


def lookup(http, client, **extra):
    return http.post("/admin/note-search", json={"client_id": client, "query": "sentinel", **extra})


def test_default_grants_only_no_model_or_note_mutations(http, config, notes):
    client, vaults, root = notes
    assert lookup(http, client).json()["searched_vault_ids"] == []
    for vault in vaults.values():
        assert http.patch("/admin/vaults/" + vault, json={"default_query": True}).status_code == 200
    original = (root / "cooling.md").read_bytes()
    response = lookup(http, client)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    value = response.json()
    assert value["searched_vault_ids"] == [vaults["personal"]]
    assert value["status"] == "evidence_found"
    assert len(value["results"]) == 1
    hit = value["results"][0]
    assert hit["note_path"] == "cooling.md" and hit["line"] == 2
    assert hit["snippet"] == "Pressure sentinel 100 psi."
    assert "Private supplier" not in response.text and "Bearer" not in response.text
    assert (root / "cooling.md").read_bytes() == original
    with database.connect(config.state_root, readonly=True) as db:
        for table in ("model_calls", "local_query_calls", "local_query_tasks", "query_jobs"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert lookup(http, client, query="nonexistent").json()["status"] == "no_evidence"


def test_identity_changes_and_revocation_do_not_reuse_results(http, config, notes):
    client, vaults, _ = notes
    http.patch("/admin/vaults/" + vaults["personal"], json={"default_query": True})
    assert lookup(http, client).json()["results"]
    other, _ = add_client(http, "other identity")
    assert lookup(http, other).json()["results"] == []
    permissions(http, client, vaults["personal"], query=False)
    assert lookup(http, client).json()["results"] == []
    with database.connect(config.state_root) as db:
        db.execute("UPDATE clients SET active=0 WHERE id=?", (client,))
    assert lookup(http, client).status_code == 404
    assert lookup(http, str(uuid4())).status_code == 404
    assert http.get("/admin/session").status_code == 200


@pytest.mark.parametrize("missing", ["origin", "csrf", "session"])
def test_admin_session_origin_and_csrf_are_required(http, notes, missing):
    client, _, _ = notes
    if missing == "origin":
        http.headers.pop("Origin")
    elif missing == "csrf":
        http.headers.pop("X-CSRF-Token")
    else:
        http.cookies.clear()
    response = lookup(http, client)
    assert response.status_code in {401, 403}
    assert "Pressure sentinel" not in response.text


@pytest.mark.parametrize(
    "extra",
    [
        {"query": ""},
        {"query": "x" * 201},
        {"limit": 0},
        {"limit": 21},
        {"limit": True},
        {"vault_ids": []},
        {"session_id": str(uuid4())},
    ],
)
def test_invalid_or_scope_widening_input_is_rejected(http, notes, extra):
    assert lookup(http, notes[0], **extra).status_code == 422


def test_search_module_is_served_as_packaged_asset(http):
    response = http.get("/static/note_search_api.js")
    assert response.status_code == 200
    assert "createNoteSearchApi" in response.text


def test_search_capability_requires_the_admin_post_route(config, store):
    from fiber_bundle.gui_capabilities import declared

    with database.connect(config.state_root, readonly=True) as db:
        assert declared(db, {"/admin/note-search": {"post": {}}})["note_search"] is True
        assert declared(db, {"/admin/note-search": {"get": {}}})["note_search"] is False
        assert declared(db, {"/v1/search": {"post": {}}})["note_search"] is False
