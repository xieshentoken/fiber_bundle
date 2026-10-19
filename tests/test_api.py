import base64
import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from fiber_bundle import auth, database
from fiber_bundle.api import create_app
from fiber_bundle.http_guard import Settings

PASSWORD = "synthetic-test-password-only"


def add_client(http, name="synthetic", create=False):
    response = http.post("/admin/clients", json={"name": name, "create_vault": create})
    assert response.status_code == 201
    data = response.json()
    return data["id"], {"Authorization": "Bearer " + data["token"]}


def vault_ids(http):
    return {v["name"]: v["id"] for v in http.get("/admin/vaults").json()}


def permissions(http, client_id, vault_id, query=False, original=False, add=False):
    response = http.put(
        "/admin/clients/" + client_id + "/vaults/" + vault_id,
        json={"query": query, "original": original, "add": add},
    )
    assert response.status_code == 200


def payload(vault_id):
    return {
        "vault_id": vault_id,
        "event_id": "event",
        "source_key": "source",
        "session_id": "session",
        "raw_event": base64.b64encode(b"synthetic-event").decode(),
        "assets": [
            {
                "data": base64.b64encode(b"synthetic-body").decode(),
                "original_name": "body.txt",
                "media_type": "text/plain",
                "role": "body",
            }
        ],
    }


def test_default_deny_separate_credentials_and_no_secret_readback(http, config):
    first, one = add_client(http)
    second, two = add_client(http)
    assert first != second and one != two
    vid = vault_ids(http)["personal"]
    assert http.get("/v1/vaults").status_code == 401
    assert http.get("/v1/vaults", headers=one).json() == []
    assert http.post("/v1/receipts", headers=one, json=payload(vid)).status_code == 404
    assert http.post("/v1/vaults", headers=one, json={"name": "extra"}).status_code == 403
    listing = http.get("/admin/clients").text
    assert one["Authorization"][7:] not in listing
    db = database.connect(config.state_root)
    try:
        assert (
            one["Authorization"][7:].encode()
            not in (config.state_root / "metadata.sqlite3").read_bytes()
        )
        assert len(db.execute("SELECT token_digest FROM clients").fetchone()[0]) == 64
    finally:
        db.close()


@pytest.mark.parametrize(
    "query,original,add",
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, False),
        (True, False, True),
        (False, True, True),
        (True, True, True),
    ],
)
def test_independent_permission_matrix(http, query, original, add):
    cid, headers = add_client(http)
    vid = vault_ids(http)["personal"]
    permissions(http, cid, vid, True, True, True)
    created = http.post("/v1/receipts", headers=headers, json=payload(vid)).json()
    version = http.get("/v1/vaults/" + vid + "/versions", headers=headers).json()[0]
    route = (
        "/v1/vaults/" + vid + "/versions/" + version["id"] + "/assets/" + version["assets"][0]["id"]
    )
    permissions(http, cid, vid, query, original, add)
    assert http.get("/v1/vaults/" + vid + "/versions", headers=headers).status_code == (
        200 if query else 404
    )
    response = http.get(route, headers=headers)
    assert response.status_code == (200 if original else 404)
    if original:
        assert response.content == b"synthetic-body"
        assert response.headers["content-disposition"].startswith("attachment")
    assert http.post("/v1/receipts", headers=headers, json=payload(vid)).status_code == (
        202 if add else 404
    )
    assert http.get("/v1/receipts/" + created["job_id"], headers=headers).status_code == (
        200 if add else 404
    )


def test_scopes_ownership_fixed_relationships_and_revoke(http, config):
    cid, one = add_client(http)
    other, two = add_client(http)
    ids = vault_ids(http)
    for identity in (cid, other):
        permissions(http, identity, ids["personal"], True, True, True)
    first = http.post("/v1/receipts", headers=one, json=payload(ids["personal"])).json()
    replay = http.post("/v1/receipts", headers=one, json=payload(ids["personal"])).json()
    second = http.post("/v1/receipts", headers=two, json=payload(ids["personal"])).json()
    assert first == replay and first["job_id"] != second["job_id"]
    assert first["source_version_id"] != second["source_version_id"]
    assert first["receipt_state"] == "stored" and first["published"] is False
    assert http.get("/v1/receipts/" + first["job_id"], headers=two).status_code == 404
    assert http.get("/admin/receipts/" + first["job_id"]).status_code == 200
    versions = http.get("/v1/vaults/" + ids["personal"] + "/versions", headers=one).json()
    assert len(versions) == 2
    assert (
        len(
            http.get(
                "/v1/vaults/" + ids["personal"] + "/versions?limit=1&offset=1", headers=one
            ).json()
        )
        == 1
    )
    assert (
        http.get("/v1/vaults/" + ids["personal"] + "/versions?limit=101", headers=one).status_code
        == 422
    )
    version = versions[0]
    route = (
        "/v1/vaults/"
        + ids["supplier"]
        + "/versions/"
        + version["id"]
        + "/assets/"
        + version["assets"][0]["id"]
    )
    permissions(http, cid, ids["supplier"], True, True, True)
    assert http.get(route, headers=one).status_code == 404
    db = database.connect(config.state_root)
    try:
        raw_id = db.execute(
            "SELECT raw_asset_id FROM receipts WHERE job_id=?", (first["job_id"],)
        ).fetchone()[0]
    finally:
        db.close()
    raw_route = (
        "/v1/vaults/"
        + ids["personal"]
        + "/versions/"
        + first["source_version_id"]
        + "/assets/"
        + raw_id
    )
    assert http.get(raw_route, headers=one).status_code == 404
    assert http.post("/admin/clients/" + cid + "/revoke").status_code == 200
    assert http.get("/v1/vaults", headers=one).status_code == 401
    assert http.get("/v1/receipts/" + first["job_id"], headers=one).status_code == 401
    assert http.get("/v1/vaults", headers=two).status_code == 200


def test_default_query_does_not_grant_or_revoke(http):
    cid, headers = add_client(http)
    vid = vault_ids(http)["personal"]
    assert http.patch("/admin/vaults/" + vid, json={"default_query": True}).status_code == 200
    assert http.get("/v1/vaults", headers=headers).json() == []
    permissions(http, cid, vid, query=True)
    assert http.get("/v1/vaults", headers=headers).json()[0]["default_query"] == 1
    assert http.patch("/admin/vaults/" + vid, json={"default_query": False}).status_code == 200
    assert http.get("/v1/vaults/" + vid + "/versions", headers=headers).status_code == 200


def test_incomplete_retry_conflict_and_failed_intake(http, monkeypatch):
    from fiber_bundle import objects

    cid, headers = add_client(http)
    vid = vault_ids(http)["personal"]
    permissions(http, cid, vid, add=True)
    body = payload(vid)
    asset = body["assets"][0]
    asset.update(
        data=None, expected_size=14, expected_sha256=hashlib.sha256(b"synthetic-body").hexdigest()
    )
    first = http.post("/v1/receipts", headers=headers, json=body).json()
    assert first["receipt_state"] == "incomplete"
    asset["data"] = base64.b64encode(b"synthetic-body").decode()
    stored = http.post("/v1/receipts", headers=headers, json=body).json()
    assert stored["job_id"] == first["job_id"] and stored["receipt_state"] == "stored"
    assert (
        http.post("/v1/receipts", headers=headers, json={**body, "session_id": "other"}).status_code
        == 409
    )

    def disk_error(*args):
        raise OSError("synthetic-private-path-token")

    monkeypatch.setattr(objects, "put_object", disk_error)
    response = http.post("/v1/receipts", headers=headers, json={**body, "event_id": "failed"})
    assert response.status_code == 503 and "private" not in response.text


@pytest.mark.parametrize(
    "extra",
    [
        {"owner_client_id": "forged"},
        {"ingress_scope": "other"},
        {"path": "/tmp/outside"},
        {"vault_id": "../../outside"},
        {"raw_event": "not base64!"},
        {"retention": True},
        {"assets": []},
    ],
)
def test_untrusted_payloads_rejected(http, extra):
    cid, headers = add_client(http)
    vid = vault_ids(http)["personal"]
    permissions(http, cid, vid, add=True)
    response = http.post("/v1/receipts", headers=headers, json={**payload(vid), **extra})
    assert response.status_code in {404, 422}
    assert "forged" not in response.text and "/tmp" not in response.text


def test_sessions_expiration_reset_csrf_and_logout(http, config):
    assert http.get("/admin/clients").status_code == 200
    assert (
        http.post(
            "/admin/clients",
            json={"name": "x", "create_vault": False},
            headers={"X-CSRF-Token": "bad"},
        ).status_code
        == 403
    )
    cookie = http.cookies.get(auth.COOKIE)
    assert cookie.encode() not in (config.state_root / "metadata.sqlite3").read_bytes()
    db = database.connect(config.state_root)
    db.execute("UPDATE admin_sessions SET expires_at=?", (time.time() - 1,))
    db.close()
    assert http.get("/admin/clients").status_code == 401
    response = http.post("/admin/login", json={"password": PASSWORD})
    assert (
        "HttpOnly" in response.headers["set-cookie"]
        and "SameSite=strict" in response.headers["set-cookie"]
    )
    http.headers["X-CSRF-Token"] = response.json()["csrf"]
    assert http.post("/admin/logout").status_code == 200
    assert http.get("/admin/clients").status_code == 401
    response = http.post("/admin/login", json={"password": PASSWORD})
    http.headers["X-CSRF-Token"] = response.json()["csrf"]
    auth.set_password(config, "synthetic-replacement-password")
    assert http.get("/admin/clients").status_code == 401
    assert http.post("/admin/login", json={"password": PASSWORD}).status_code == 401


def test_host_origin_cors_loopback_body_limits_and_safe_errors(http):
    for headers, status in [
        ({"Host": "evil.example"}, 400),
        ({"Origin": "null"}, 403),
        ({"Origin": "http://evil.example"}, 403),
        ({"X-Forwarded-Host": "evil.example", "X-Forwarded-For": "1.2.3.4"}, 200),
    ]:
        assert http.get("/health", headers=headers).status_code == status
    assert http.options("/admin/clients").status_code == 405
    response = http.post("/admin/login", content="sensitive-bad-json")
    assert response.status_code == 422 and "sensitive" not in response.text
    assert "access-control-allow-origin" not in response.headers
    assert (
        http.post("/admin/login", content="{}", headers={"Content-Length": "999999999"}).status_code
        == 413
    )
    assert (
        http.post("/admin/login", content="{}", headers={"Content-Length": "oops"}).status_code
        == 400
    )
    http.headers.pop("Origin")
    assert http.post("/admin/login", json={"password": PASSWORD}).status_code == 403


def test_no_admin_default_and_rate_limit(config, store):
    with TestClient(
        create_app(config, Settings(login_attempts=2)),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 49001),
    ) as client:
        headers = {"Origin": "http://127.0.0.1:8765"}
        for _ in range(2):
            assert (
                client.post(
                    "/admin/login", json={"password": PASSWORD}, headers=headers
                ).status_code
                == 401
            )
        assert (
            client.post("/admin/login", json={"password": PASSWORD}, headers=headers).status_code
            == 429
        )
        assert client.get("/admin/clients").status_code == 401


def test_agent_token_cannot_be_admin(http, config):
    _, headers = add_client(http, create=True)
    http.cookies.clear()
    assert http.get("/admin/clients", headers=headers).status_code == 401
    assert http.get("/v1/vaults", headers=headers).status_code == 200


def test_packaged_page_and_headers(http):
    response = http.get("/")
    assert response.status_code == 200
    assert "本机管理" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert http.get("/static/app.js").status_code == 200
    assert http.get("/static/media.js").status_code == 200
    assert http.get("/static/style.css").status_code == 200
    assert http.get("/static/metadata.sqlite3").status_code == 404
    assert http.get("/docs").status_code == 404
