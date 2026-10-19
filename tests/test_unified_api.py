"""Admin-only merge reads and mutations keep the existing authentication and CSRF boundary."""

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_vault_merge_plan import fingerprints, prepared

from fiber_bundle import automatic_ingestion


@pytest.fixture(autouse=True)
def explicit_ticks(monkeypatch):
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)


def test_merge_routes_require_admin_and_concrete_hash_confirmation(
    http, config, article_scope, policy
):
    cid, source, target, _, selection = prepared(config, article_scope, policy)
    before = fingerprints(config)
    response = http.post("/admin/vault-merges/preview", json=selection.model_dump())
    assert response.status_code == 200 and response.json()["ready"]
    packet = response.json()
    denied = http.post(
        "/admin/vault-merges/freeze",
        json={"selection": selection.model_dump(), "plan_sha256": packet["plan_sha256"]},
        headers={"X-CSRF-Token": "invalid"},
    )
    assert denied.status_code == 403
    frozen = http.post(
        "/admin/vault-merges/freeze",
        json={"selection": selection.model_dump(), "plan_sha256": packet["plan_sha256"]},
    )
    assert frozen.status_code == 200 and frozen.json()["state"] == "prepared"
    identity = frozen.json()["id"]
    assert (
        http.post(
            f"/admin/vault-merges/{identity}/cancel", json={"plan_sha256": "0" * 64}
        ).status_code
        == 422
    )
    assert (
        http.post(
            f"/admin/vault-merges/{identity}/cancel", json={"plan_sha256": packet["plan_sha256"]}
        ).json()["state"]
        == "cancelled"
    )
    assert fingerprints(config) == before
    http.cookies.clear()
    assert http.get("/admin/vault-merges").status_code == 401
    assert (
        http.get(f"/admin/vault-merges/inventory/{source}", params={"client_id": cid}).status_code
        == 401
    )
