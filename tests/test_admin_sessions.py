"""Session restoration across windows keeps writes bound to the same live login."""

import time

import pytest
from fastapi.testclient import TestClient

from fiber_bundle import auth, database
from fiber_bundle.api import create_app

PASSWORD = "synthetic-test-password-only"
ORIGIN = "http://127.0.0.1:8765"


def session_rows(config):
    connection = database.connect(config.state_root)
    try:
        return [tuple(row) for row in connection.execute("SELECT * FROM admin_sessions")]
    finally:
        connection.close()


def write(http, csrf, name="synthetic window"):
    return http.post(
        "/admin/clients",
        headers={"X-CSRF-Token": csrf},
        json={"name": name, "create_vault": False},
    )


def test_restore_other_window_preserves_both_tokens_and_session_row(http, config):
    initial = http.headers["X-CSRF-Token"]
    before = session_rows(config)
    restored = http.get("/admin/session")
    assert restored.status_code == 200
    assert write(http, initial, "first window").status_code == 201
    assert write(http, restored.json()["csrf"], "second window").status_code == 201
    assert session_rows(config) == before
    for _ in range(3):
        assert http.get("/admin/session").json() == restored.json()
    assert restored.json()["expires_at"] == before[0][2]
    assert "no-store" in restored.headers["cache-control"]


def test_restore_survives_new_application_instance_without_secret_storage(http, config):
    value = http.get("/admin/session").json()
    with TestClient(create_app(config), base_url=ORIGIN, client=("127.0.0.1", 49001)) as restarted:
        restarted.cookies.update(http.cookies)
        restarted.headers["Origin"] = ORIGIN
        assert restarted.get("/admin/session").json() == value
        assert write(restarted, value["csrf"]).status_code == 201
    raw = (config.state_root / "metadata.sqlite3").read_bytes()
    assert value["csrf"].encode() not in raw
    assert http.cookies.get(auth.COOKIE).encode() not in raw


def test_old_session_digest_keeps_legacy_token_after_restore(http, config):
    legacy = "synthetic-old-version-csrf"
    connection = database.connect(config.state_root)
    connection.execute("UPDATE admin_sessions SET csrf_digest=?", (auth.digest(legacy),))
    connection.close()
    before = session_rows(config)
    stable = http.get("/admin/session").json()["csrf"]
    assert stable != legacy
    assert write(http, legacy).status_code == 201
    assert write(http, stable).status_code == 201
    assert session_rows(config) == before


@pytest.mark.parametrize("csrf", ["", "bad", "synthetic-cookie", "0" * 64])
def test_unknown_token_rejected_after_restore(http, csrf):
    http.get("/admin/session")
    assert write(http, csrf).status_code == 403


def test_unicode_forged_token_is_denied_by_auth_boundary(http, config):
    connection = database.connect(config.state_root)
    try:
        with pytest.raises(auth.Denied) as rejected:
            auth.admin(connection, http.cookies.get(auth.COOKIE), "伪造令牌")
        assert rejected.value.status == 403
    finally:
        connection.close()


def test_cookie_digest_and_restoration_seed_cannot_authorize_write(http, config):
    http.get("/admin/session")
    row = session_rows(config)[0]
    for csrf in [row[0], row[1], http.cookies.get(auth.COOKIE)]:
        assert write(http, csrf).status_code == 403


def test_tokens_from_separate_logins_cannot_be_exchanged(http, config):
    first_initial = http.headers["X-CSRF-Token"]
    first_stable = http.get("/admin/session").json()["csrf"]
    with TestClient(create_app(config), base_url=ORIGIN, client=("127.0.0.1", 49002)) as other:
        other.headers["Origin"] = ORIGIN
        logged = other.post("/admin/login", json={"password": PASSWORD}).json()
        second_stable = other.get("/admin/session").json()["csrf"]
        assert first_stable != second_stable
        for csrf in [first_initial, first_stable]:
            assert write(other, csrf).status_code == 403
        for csrf in [logged["csrf"], second_stable]:
            assert write(http, csrf).status_code == 403
        assert write(other, second_stable).status_code == 201
    assert write(http, first_stable).status_code == 201


@pytest.mark.parametrize("end", ["logout", "expire", "password", "relogin"])
def test_both_tokens_end_with_session_and_restoration_does_not_extend_it(http, config, end):
    initial = http.headers["X-CSRF-Token"]
    restored = http.get("/admin/session").json()
    if end == "logout":
        assert http.post("/admin/logout", headers={"X-CSRF-Token": initial}).status_code == 200
    elif end == "expire":
        connection = database.connect(config.state_root)
        connection.execute("UPDATE admin_sessions SET expires_at=?", (time.time() - 1,))
        connection.close()
    elif end == "password":
        auth.set_password(config, "synthetic-replacement-password")
    else:
        logged = http.post("/admin/login", json={"password": PASSWORD})
        assert logged.status_code == 200
        assert logged.json()["csrf"] not in [initial, restored["csrf"]]
    for csrf in [initial, restored["csrf"]]:
        assert write(http, csrf).status_code == (403 if end == "relogin" else 401)
    if end != "relogin":
        assert http.get("/admin/session").status_code == 401


def test_current_token_still_requires_origin_and_cookie(http):
    csrf = http.get("/admin/session").json()["csrf"]
    http.headers["X-CSRF-Token"] = csrf
    http.headers["Origin"] = "http://evil.example"
    assert write(http, csrf).status_code == 403
    http.headers.pop("Origin")
    assert write(http, csrf).status_code == 403
    http.headers["Origin"] = ORIGIN
    http.cookies.clear()
    assert write(http, csrf).status_code == 401
