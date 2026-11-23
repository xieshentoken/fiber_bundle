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


@pytest.mark.parametrize("ttl", [-1, 2592001, True, "3600", 1.5, None])
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


@pytest.mark.parametrize("ttl", [604800, 1209600, 2592000, 0])
def test_extended_duration_login_restore_and_revocation(http, config, ttl):
    from fiber_bundle.session_policy import cookie_ttl

    before = http.get("/admin/session").json()
    saved = http.put("/admin/system/settings", json={"session_ttl": ttl, "expected_revision": 0})
    assert saved.status_code == 200
    assert http.get("/admin/session").json() == before
    now = time.time()
    logged = http.post("/admin/login", json={"password": PASSWORD})
    assert logged.status_code == 200
    value = logged.json()
    if ttl:
        assert now + ttl <= value["expires_at"] <= time.time() + ttl
    else:
        assert value["expires_at"] == 0
    cookie = logged.headers["set-cookie"]
    assert f"Max-Age={cookie_ttl(ttl)}" in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    http.headers["X-CSRF-Token"] = value["csrf"]
    restored = http.get("/admin/session")
    assert restored.status_code == 200
    assert restored.json()["expires_at"] == value["expires_at"]
    assert ("set-cookie" in restored.headers) is (ttl == 0)
    if ttl == 0:
        # The cleanup performed by another login must not delete permanent sessions.
        with TestClient(create_app(config), base_url=ORIGIN, client=("127.0.0.1", 49004)) as other:
            other.headers["Origin"] = ORIGIN
            assert other.post("/admin/login", json={"password": PASSWORD}).status_code == 200
        assert http.get("/admin/session").status_code == 200
    assert http.post("/admin/logout").status_code == 200
    assert http.get("/admin/session").status_code == 401


def test_permanent_session_survives_restart_but_password_change_revokes(http, config):
    http.put("/admin/system/settings", json={"session_ttl": 0, "expected_revision": 0})
    logged = http.post("/admin/login", json={"password": PASSWORD}).json()
    with TestClient(create_app(config), base_url=ORIGIN, client=("127.0.0.1", 49005)) as other:
        other.cookies.update(http.cookies)
        other.headers["Origin"] = ORIGIN
        assert other.get("/admin/session").json()["expires_at"] == 0
        assert (
            other.put(
                "/admin/system/settings",
                headers={"X-CSRF-Token": "wrong"},
                json={"session_ttl": 3600, "expected_revision": 1},
            ).status_code
            == 403
        )
        auth.set_password(config, "synthetic-replacement-password")
        assert other.get("/admin/session").status_code == 401
        assert (
            other.put(
                "/admin/system/settings",
                headers={"X-CSRF-Token": logged["csrf"]},
                json={"session_ttl": 3600, "expected_revision": 1},
            ).status_code
            == 401
        )


def test_schema35_policy_migration_preserves_sessions_and_saved_policy(http, config):
    db = database.connect(config.state_root)
    before = [tuple(row) for row in db.execute("SELECT * FROM admin_sessions")]
    db.execute("DROP TABLE system_settings")
    # Construct the actual old policy constraint rather than just lowering the version.
    db.execute(
        "CREATE TABLE system_settings (id INTEGER PRIMARY KEY CHECK(id=1), "
        "session_ttl INTEGER NOT NULL CHECK(session_ttl BETWEEN 1 AND 86400), "
        "revision INTEGER NOT NULL CHECK(revision>0)) STRICT"
    )
    db.execute("INSERT INTO system_settings VALUES(1,28800,7)")
    db.execute("PRAGMA user_version=35")
    db.close()
    backup = database.migrate(config)
    assert backup is not None
    with sqlite3.connect(backup) as prior:
        assert prior.execute("PRAGMA user_version").fetchone()[0] == 35
        assert prior.execute("SELECT * FROM system_settings").fetchall() == [(1, 28800, 7)]
    db = database.connect(config.state_root)
    assert tuple(db.execute("SELECT * FROM system_settings").fetchone()) == (1, 28800, 7)
    assert [tuple(row) for row in db.execute("SELECT * FROM admin_sessions")] == before
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    db.execute("UPDATE system_settings SET session_ttl=0")
    db.close()
    assert database.migrate(config) is None
