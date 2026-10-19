"""Login policy is authenticated, durable, and never extends an existing session."""

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from fiber_bundle import auth, database
from fiber_bundle.api import create_app
from fiber_bundle.http_guard import Settings

PASSWORD = "synthetic-test-password-only"
ORIGIN = "http://127.0.0.1:8765"


def test_login_policy_persists_and_only_applies_to_next_login(http, config):
    before = http.get("/admin/session").json()
    assert http.get("/admin/system/settings").json()["session_ttl"] == 3600
    saved = http.put("/admin/system/settings", json={"session_ttl": 900, "expected_revision": 0})
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert http.get("/admin/session").json() == before
    assert (
        http.put(
            "/admin/system/settings", json={"session_ttl": 7200, "expected_revision": 0}
        ).status_code
        == 409
    )
    with TestClient(create_app(config), base_url=ORIGIN, client=("127.0.0.1", 49002)) as other:
        other.headers["Origin"] = ORIGIN
        now = time.time()
        logged = other.post("/admin/login", json={"password": PASSWORD})
        assert logged.status_code == 200
        assert now + 899 <= logged.json()["expires_at"] <= time.time() + 901
        assert "Max-Age=900" in logged.headers["set-cookie"]


@pytest.mark.parametrize("ttl", [0, -1, 86401, True, "3600", 1.5, None])
def test_invalid_policy_is_rejected(http, ttl):
    assert (
        http.put(
            "/admin/system/settings", json={"session_ttl": ttl, "expected_revision": 0}
        ).status_code
        == 422
    )
    assert http.get("/admin/system/settings").json()["revision"] == 0


def test_policy_requires_admin_cookie_csrf_and_origin(http):
    body = {"session_ttl": 900, "expected_revision": 0}
    assert (
        http.put("/admin/system/settings", headers={"X-CSRF-Token": "wrong"}, json=body).status_code
        == 403
    )
    assert (
        http.put(
            "/admin/system/settings", headers={"Origin": "http://evil.example"}, json=body
        ).status_code
        == 403
    )
    http.cookies.clear()
    assert http.get("/admin/system/settings").status_code == 401
    assert http.put("/admin/system/settings", json=body).status_code == 401


def test_startup_default_is_retained_until_saved(config, store):
    auth.set_password(config, PASSWORD)
    with TestClient(
        create_app(config, Settings(session_ttl=7200)),
        base_url=ORIGIN,
        client=("127.0.0.1", 49003),
    ) as http:
        http.headers["Origin"] = ORIGIN
        assert http.post("/admin/login", json={"password": PASSWORD}).status_code == 200
        assert http.get("/admin/system/settings").json()["session_ttl"] == 7200


def test_schema27_upgrade_is_additive_and_backed_up(config, store):
    db = database.connect(config.state_root)
    from schema_support import remove_automatic_schema

    remove_automatic_schema(db)
    before = [tuple(r) for r in db.execute("SELECT * FROM vaults")]
    db.execute("DROP TABLE model_call_credentials")
    db.execute("DROP TABLE system_settings")
    db.execute("DROP TABLE vault_directory_previews")
    db.execute("DROP TABLE publication_locations")
    db.execute("DROP TABLE vault_save_policies")
    db.execute("PRAGMA user_version=27")
    db.close()
    backup = database.migrate(config)
    assert backup and backup.is_file()
    with sqlite3.connect(backup) as prior:
        assert prior.execute("PRAGMA user_version").fetchone()[0] == 27
        assert prior.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db = database.connect(config.state_root)
    assert [tuple(r) for r in db.execute("SELECT * FROM vaults")] == before
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.execute("SELECT * FROM model_call_credentials").fetchall() == []
    assert db.execute("SELECT * FROM system_settings").fetchall() == []
    db.close()
    assert database.migrate(config) is None
