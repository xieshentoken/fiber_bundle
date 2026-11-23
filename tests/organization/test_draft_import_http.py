import time
from uuid import uuid4

from test_draft_import_preview import draft as draft

from fiber_bundle import database
from fiber_bundle.organization import draft_import_files


def wait(http, scope, identity, wanted):
    for _ in range(100):
        response = http.get("/admin/draft-imports/" + identity, params=scope)
        assert response.status_code == 200, response.text
        value = response.json()
        if value["state"] not in {"queued", "working"}:
            assert value["state"] == wanted, value
            return value
        time.sleep(0.05)
    raise AssertionError("Background phase did not finish")


def test_http_preview_confirm_and_list_are_scoped(http, config, draft, monkeypatch):
    scope = {"client_id": draft[0], "vault_id": draft[1]}
    identity = str(uuid4())
    body = {**scope, "submission_id": identity, "source_path": str(draft[3]), "folder": ""}
    response = http.post("/admin/draft-imports", json=body)
    assert response.status_code == 202, response.text
    value = wait(http, scope, identity, "preview")
    assert not (draft[2] / value["relative_path"]).exists()
    confirm = {
        **scope,
        "preview_sha256": value["preview_sha256"],
        "moc_ids": [],
        "candidate_acknowledged": True,
    }
    assert (
        http.post(
            "/admin/draft-imports/" + identity + "/confirm",
            json={**confirm, "candidate_acknowledged": 1},
        ).status_code
        == 422
    )
    assert (
        http.post("/admin/draft-imports/" + identity + "/confirm", json=confirm).status_code == 202
    )
    value = wait(http, scope, identity, "completed")
    assert value["index_state"] == "ready"
    monkeypatch.setattr(
        draft_import_files,
        "inspect",
        lambda *a: (_ for _ in ()).throw(AssertionError("GET inspected files")),
    )
    response = http.get("/admin/draft-imports", params=scope)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["id"] == identity
    assert "source_files" not in response.json()["items"][0]
    assert "manifest" not in response.json()["items"][0]["package"]
    with database.connect(config.state_root) as db:
        other = db.execute("select id from vaults where name='supplier'").fetchone()[0]
    assert http.get(
        "/admin/draft-imports/" + identity, params={**scope, "vault_id": other}
    ).status_code in (403, 404)


def test_http_requires_csrf_and_preview_hash(http, draft):
    scope = {"client_id": draft[0], "vault_id": draft[1]}
    identity = str(uuid4())
    body = {**scope, "submission_id": identity, "source_path": str(draft[3])}
    csrf = http.headers.pop("X-CSRF-Token")
    assert http.post("/admin/draft-imports", json=body).status_code == 403
    http.headers["X-CSRF-Token"] = csrf
    assert http.post("/admin/draft-imports", json=body).status_code == 202
    value = wait(http, scope, identity, "preview")
    response = http.post(
        "/admin/draft-imports/" + identity + "/confirm",
        json={**scope, "preview_sha256": "0" * 64, "moc_ids": [], "candidate_acknowledged": True},
    )
    assert response.status_code in (409, 422), response.text
    assert not (draft[2] / value["relative_path"]).exists()


def test_copy_does_not_hold_http_writer_queue_and_can_cancel(http, draft, monkeypatch):
    import threading

    entered = threading.Event()
    release = threading.Event()
    original = draft_import_files.copy_member

    def wait_copy(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(draft_import_files, "copy_member", wait_copy)
    scope = {"client_id": draft[0], "vault_id": draft[1]}
    identity = str(uuid4())
    assert (
        http.post(
            "/admin/draft-imports",
            json={**scope, "submission_id": identity, "source_path": str(draft[3])},
        ).status_code
        == 202
    )
    p = wait(http, scope, identity, "preview")
    assert (
        http.post(
            "/admin/draft-imports/" + identity + "/confirm",
            json={
                **scope,
                "preview_sha256": p["preview_sha256"],
                "moc_ids": [],
                "candidate_acknowledged": True,
            },
        ).status_code
        == 202
    )
    assert entered.wait(3)
    started = time.monotonic()
    try:
        assert http.get("/health").status_code == 200
        assert http.get("/admin/draft-imports", params=scope).status_code == 200
        assert (
            http.post("/admin/draft-imports/" + identity + "/cancel", json=scope).status_code == 202
        )
        assert time.monotonic() - started < 2
    finally:
        release.set()
    result = wait(http, scope, identity, "cancelled")
    assert not (draft[2] / result["relative_path"]).exists()
