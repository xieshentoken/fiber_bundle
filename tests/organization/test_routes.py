from test_jobs import ready as ready

from fiber_bundle import auth, database


def test_admin_preview_and_classification_permission(config, http, ready):
    cid, vid, _, m, n = ready
    scope = {"client_id": cid, "vault_id": vid}
    before = m.read_bytes()
    r = http.get("/admin/organization", params=scope)
    assert r.status_code == 200, r.text
    assert r.json()["policy"]["settings"]["mode"] == "off"
    assert m.read_bytes() == before
    r = http.post("/admin/organization/jobs", json={**scope, "target": "Inbox", "preview": True})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "preview"
    job = r.json()["id"]
    r = http.post(f"/admin/organization/jobs/{job}/confirm", json=scope)
    assert r.status_code == 200, r.text
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=True, original=True, add=False)
    r = http.post("/admin/organization/jobs", json=scope)
    assert r.status_code == 404, r.text


def test_invalid_policy_is_validation_error(http, ready):
    cid, vid, *_ = ready
    r = http.put(
        "/admin/organization/policy",
        json={
            "client_id": cid,
            "vault_id": vid,
            "expected_revision": 0,
            "settings": {"include": ["../no"]},
        },
    )
    assert r.status_code == 422, r.text


def test_progress_reads_ledger_only_and_enforces_current_grant(http, config, ready, monkeypatch):
    from fiber_bundle.organization import jobs, maintenance

    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "additive")
    monkeypatch.setattr(
        jobs,
        "snapshot",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("progress read the vault")),
    )
    scope = {"client_id": cid, "vault_id": vid}
    result = http.get("/admin/organization/progress", params=scope)
    assert result.status_code == 200
    assert result.json()["jobs"][0]["id"] == job["id"]
    assert not result.json()["model_called"]
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=False, original=False, add=False)
    assert http.get("/admin/organization/progress", params=scope).status_code == 404
