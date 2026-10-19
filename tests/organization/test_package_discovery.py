import json
from unittest.mock import patch

import pytest
from test_jobs import ready as ready

from fiber_bundle import auth, database, moc_store, note_index
from fiber_bundle.organization import (
    catalog,
    jobs,
    manual_membership,
    package_discovery,
    packages,
    processing,
    store,
)
from fiber_bundle.vault_scope import Scope


def test_unregistered_package_is_not_ordinary_notes(config, scope, package):
    cid, vid, root = scope
    reads = []
    original = note_index.read_note
    with patch.object(
        note_index, "read_note", side_effect=lambda *a: (reads.append(a[1]), original(*a))[1]
    ):
        with database.connect(config.state_root) as db:
            entities, _, _ = catalog.snapshot(config, db, cid, vid)
    assert not any(e["path"].startswith("Book/fake-thermal/") for e in entities)
    assert not any(p.startswith("Book/fake-thermal/") for p in reads)
    assert store.rows(config, "packages", vid) == []  # GET remains read-only.


def test_scan_whole_package_cache_and_version_history(config, scope, package):
    cid, vid, _ = scope
    before = {p: p.read_bytes() for p in package.rglob("*") if p.is_file()}
    first = package_discovery.scan(config, cid, vid)
    assert first["items"][0]["state"] == "registered", first
    versions = store.rows(config, "versions", vid)
    with patch.object(packages, "inspect", side_effect=AssertionError("Repeated full inspection")):
        assert package_discovery.scan(config, cid, vid)["items"] == first["items"]
    assert versions == store.rows(config, "versions", vid)
    assert before == {p: p.read_bytes() for p in before}
    value = json.loads((package / "manifest.json").read_text())
    value["package"]["version"] = "2"
    (package / "manifest.json").write_text(json.dumps(value))
    second = package_discovery.scan(config, cid, vid)
    assert second["items"][0]["id"] == first["items"][0]["id"]
    assert len(store.rows(config, "versions", vid)) == 2


@pytest.mark.parametrize(
    "damage", ["invalid_json", "missing_manifest", "missing", "changed", "symlink", "traversal"]
)
def test_failed_package_is_quarantined_and_reports_reason(config, scope, package, damage):
    cid, vid, _ = scope
    marker = package / "manifest.json"
    if damage == "invalid_json":
        marker.write_text("{")
    elif damage == "missing_manifest":
        marker.unlink()
    elif damage == "missing":
        (package / "references/runtime/catalog.json").unlink()
    elif damage == "changed":
        (package / "assets/test.png").write_bytes(b"tampered")
    elif damage == "symlink":
        (package / "assets/test.png").unlink()
        (package / "assets/test.png").symlink_to("/etc/hosts")
    else:
        manifest = json.loads(marker.read_text())
        manifest["integrity"]["files"]["../../outside.md"] = "a" * 64
        marker.write_text(json.dumps(manifest))
    result = package_discovery.scan(config, cid, vid)
    assert result["items"][0]["state"] == "failed"
    assert result["items"][0]["error"]
    assert not store.rows(config, "packages", vid)
    with database.connect(config.state_root) as db:
        entities, _, _ = catalog.snapshot(config, db, cid, vid)
    assert not any(e["path"].startswith("Book/fake-thermal/") for e in entities)


def test_scope_is_pruned_before_probe_and_symlink_not_followed(config, scope, package, monkeypatch):
    root = scope[2]
    (root / "Book/alias").symlink_to(package, target_is_directory=True)
    original = package_discovery.detected

    def detect(root, path, allowed):
        assert path != "Book/fake-thermal"
        return original(root, path, allowed)

    monkeypatch.setattr(package_discovery, "detected", detect)
    assert package_discovery.candidates(root, Scope(exclude=("Book/fake-thermal",)), "Book") == []


def test_tick_discovers_with_automatic_policy_and_request_is_fast(config, scope, package):
    cid, vid, _ = scope
    with database.connect(config.state_root) as db:
        jobs.save_policy(config, db, cid, vid, {"mode": "automatic"}, 0)
        with patch.object(packages, "inspect", side_effect=AssertionError("request did I/O")):
            assert package_discovery.request(config, db, cid, vid)["state"] == "queued"
    package_discovery.tick(config)
    assert package_discovery.status(config, cid, vid)["state"] == "ready"
    assert len(store.rows(config, "packages", vid)) == 1
    with patch.object(package_discovery, "scan", side_effect=AssertionError("busy polling")):
        package_discovery.tick(config)
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=True, original=True, add=False)
    with pytest.raises(auth.Denied):
        package_discovery.scan(config, cid, vid)


def test_manual_multiple_mocs_take_over_queued_package_without_model(config, ready, package):
    cid, vid, root, _, _ = ready
    (root / "Map/材料 MOC.md").write_text("---\ntype: moc\n---\n# 材料 MOC\n人工正文\n")
    payload = {p: p.read_bytes() for p in package.rglob("*") if p.is_file()}
    with database.connect(config.state_root) as db:
        jobs.save_policy(config, db, cid, vid, {"mode": "automatic"}, 0)
    package_discovery.scan(config, cid, vid)
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        expert = next(e for e in entities if e["kind"] == "expert")
        queued = jobs.submit(config, db, cid, vid, expert["path"], source="automatic")
        assert queued["items"]
        mids = [m["id"] for m in mocs]
        assert manual_membership.apply(config, db, cid, vid, expert["id"], mids, "link")[
            "completed"
        ]
        before = [p.read_bytes() for p in root.glob("Map/*.md")]
        repeat = manual_membership.apply(config, db, cid, vid, expert["id"], mids + mids, "link")
        assert repeat["completed"] and not repeat["changed"]
        assert before == [p.read_bytes() for p in root.glob("Map/*.md")]
    with patch.object(
        processing.model_review, "resolve", side_effect=AssertionError("model called")
    ):
        processing.prepare(config, queued["id"])
    with database.connect(config.state_root) as db:
        done = jobs.run(config, db, store.get(config, "jobs", queued["id"]))
        assert done["state"] == "completed" and done["items"][0]["manual_completed"]
        assert not jobs.submit(config, db, cid, vid, expert["path"], source="automatic")["items"]
        assert jobs.policy(config, vid)["settings"]["mode"] == "automatic"
        for mid in mids:
            assert "manual" in moc_store.load(config, vid, mid).members[expert["id"]]
    assert payload == {p: p.read_bytes() for p in payload}


def test_inflight_result_cannot_override_manual_mocs(config, ready, package, monkeypatch):
    cid, vid, _, _, _ = ready
    package_discovery.scan(config, cid, vid)
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        expert = next(e for e in entities if e["kind"] == "expert")
        job = jobs.submit(config, db, cid, vid, expert["path"], source="automatic")
        job["items"][0].update(selected=[], evaluated=False)  # Exercise the model branch.
        store.put(config, "jobs", job["id"], vid, job)
    monkeypatch.setattr(processing.semantic, "recall", lambda *a: {"state": "unavailable"})

    def model(*args):
        with database.locked(config), database.connect(config.state_root) as db:
            assert manual_membership.apply(
                config, db, cid, vid, expert["id"], [mocs[0]["id"]], "link"
            )["completed"]
        args[-1]()  # Simulate post-call source verification after a concurrent human choice.
        raise AssertionError("manual completion was not detected")

    monkeypatch.setattr(processing.model_review, "resolve", model)
    processing.prepare(config, job["id"])
    with database.connect(config.state_root) as db:
        done = jobs.run(config, db, store.get(config, "jobs", job["id"]))
    assert done["state"] == "completed"
    assert done["items"][0].get("manual_completed"), done
    assert not done["items"][0].get("error")


def test_registered_broken_copy_does_not_break_overview_or_replace_version(config, scope, package):
    cid, vid, _ = scope
    first = package_discovery.scan(config, cid, vid)
    before = store.rows(config, "packages", vid)
    (package / "references/runtime/catalog.json").write_text("{}")
    result = package_discovery.scan(config, cid, vid)
    assert result["items"][0]["state"] == "failed"
    assert store.rows(config, "packages", vid) == before
    with database.connect(config.state_root) as db:
        entities, _, _ = catalog.snapshot(config, db, cid, vid)
    assert not any(e["id"] == first["items"][0]["module_id"] for e in entities)


def test_scan_route_enqueues_and_progress_is_read_only(http, config, scope, package, monkeypatch):
    cid, vid, _ = scope
    body = {"client_id": cid, "vault_id": vid}
    assert http.post("/admin/organization/packages/scan", json=body).json()["state"] == "queued"
    package_discovery.tick(config)
    rows = store.rows(config, "package_scans", vid)
    monkeypatch.setattr(package_discovery, "scan", lambda *a: pytest.fail("GET scanned Book"))
    response = http.get("/admin/organization/progress", params=body)
    assert response.status_code == 200
    assert response.json()["package_scan"]["state"] == "ready"
    assert "fingerprint" not in response.json()["package_scan"]["items"][0]
    assert store.rows(config, "package_scans", vid) == rows
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=False, original=False, add=False)
    assert http.post("/admin/organization/packages/scan", json=body).status_code == 404


def test_discovery_runs_while_ingestion_worker_is_waiting(config, scope, package, monkeypatch):
    import threading
    import time

    from fiber_bundle import automatic_ingestion

    cid, vid, _ = scope
    with database.connect(config.state_root) as db:
        jobs.save_policy(config, db, cid, vid, {"mode": "automatic"}, 0)
    waiting, release = threading.Event(), threading.Event()

    def wait_for_external_work(*args):
        waiting.set()
        release.wait(10)
        return False

    monkeypatch.setattr(automatic_ingestion, "tick", wait_for_external_work)
    runtime = automatic_ingestion.Runtime(config)
    runtime.start()
    try:
        assert waiting.wait(3)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and not store.rows(config, "packages", vid):
            time.sleep(0.05)
        assert len(store.rows(config, "packages", vid)) == 1
    finally:
        runtime.close()
        release.set()
        runtime.thread.join(3)
    assert not runtime.discovery_thread.is_alive()


def test_identical_copy_keeps_one_identity_and_reuses_both_inspections(config, scope, package):
    import shutil

    cid, vid, root = scope
    shutil.copytree(package, root / "Book/duplicate")
    first = package_discovery.scan(config, cid, vid)
    assert len(first["items"]) == 2
    assert len({i["id"] for i in first["items"]}) == 1
    assert {i["path"] for i in first["items"]} == {"Book/duplicate", "Book/fake-thermal"}
    assert len(store.rows(config, "packages", vid)) == 1
    assert len(store.rows(config, "versions", vid)) == 1
    with patch.object(packages, "inspect", side_effect=AssertionError("copy re-inspected")):
        assert package_discovery.scan(config, cid, vid)["items"] == first["items"]


def test_manual_receipt_never_hides_later_explicit_cancellation(config, ready, package):
    from fiber_bundle.organization import manual_resolution

    cid, vid, _, _, _ = ready
    package_discovery.scan(config, cid, vid)
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        expert = next(e for e in entities if e["kind"] == "expert")
        job = jobs.submit(config, db, cid, vid, expert["path"])
        assert manual_membership.apply(config, db, cid, vid, expert["id"], [mocs[0]["id"]], "link")[
            "completed"
        ]
        cancelled = jobs.action(config, db, cid, vid, job["id"], "cancel")
    assert manual_resolution.project(config, cancelled)["state"] == "cancelled"
