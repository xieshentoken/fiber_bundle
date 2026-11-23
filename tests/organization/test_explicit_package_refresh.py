"""Index-button admission is serialized with copied package discovery, never classification."""

import json
import shutil
import time
from unittest.mock import patch

from fiber_bundle import database
from fiber_bundle.organization import jobs, package_discovery, packages, store
from fiber_bundle.vault_scope import Scope
from fiber_bundle.vault_sync import (
    commands,
    explicit_refresh,
    index,
    maintenance,
    presentation,
    scanner,
)
from fiber_bundle.vault_sync import store as sync


def metadata(config, vid, root):
    sync.register(config, vid, str(root), Scope(), "test")
    drain(config)


def drain(config):
    while tasks := sync.due(config, now=time.time() + 100):
        for task in tasks:
            scanner.apply(config, task)


def test_copied_draft_is_admitted_before_index_without_automatic_model_trigger(
    config, scope, package
):
    cid, vid, root = scope
    staged = config.project_root / "staged-draft"
    package.rename(staged)
    metadata(config, vid, root)
    shutil.copytree(staged, package)
    with database.connect(config.state_root) as db:
        jobs.save_policy(config, db, cid, vid, {"mode": "automatic"}, 0)
    original = {p: p.read_bytes() for p in package.rglob("*") if p.is_file()}
    commands.submit(config, cid, vid, reobserve=True)
    drain(config)
    assert explicit_refresh.activity(config, vid) == {"stage": "packages", "state": "queued"}
    assert maintenance.next_task(config, {vid}, time.time() + 100) is None
    with patch.object(jobs, "notify", side_effect=AssertionError("classification trigger")):
        package_discovery.tick(config)
    assert explicit_refresh.ready(config, vid)
    assert maintenance.next_task(config, {vid}, time.time() + 100) == (vid, cid)
    registered = store.rows(config, "packages", vid)
    assert len(registered) == 1 and registered[0]["path"] == "Book/fake-thermal"
    assert not store.rows(config, "triggers", vid)
    assert original == {p: p.read_bytes() for p in original}


def test_invalid_copied_package_is_partial_coverage_without_fictional_note_issue(
    config, scope, package
):
    cid, vid, root = scope
    metadata(config, vid, root)
    (package / "assets/test.png").write_bytes(b"incomplete copy")
    commands.submit(config, cid, vid, reobserve=True)
    drain(config)
    package_discovery.tick(config)
    assert explicit_refresh.ready(config, vid)
    assert explicit_refresh.package_issues(config, vid) == [
        {"path": "Book/fake-thermal", "reason": "expert_package_scan_failed"}
    ]
    assert not store.rows(config, "packages", vid)
    with sync.connection(config) as side:
        assert not side.execute("SELECT 1 FROM issues").fetchone()
    result = index.update(config, cid, vid)
    assert result["state"] == "partial" and result["needs_attention"] == 1
    status = presentation.index_status(config, vid)
    assert status["state"] == "partial" and status["sync"]["needs_attention"] == 1
    assert status["sync"]["issues"][0]["kind"] == "package"


def test_inflight_automatic_scan_observes_new_explicit_suppression_before_commit(
    config, scope, package
):
    cid, vid, root = scope
    metadata(config, vid, root)
    with database.connect(config.state_root) as db:
        jobs.save_policy(config, db, cid, vid, {"mode": "automatic"}, 0)
    original = packages.inspect
    admitted = []

    def inspect(*args):
        value = original(*args)
        admitted.append(commands.submit(config, cid, vid, reobserve=True))
        return value

    with (
        patch.object(packages, "inspect", side_effect=inspect),
        patch.object(jobs, "notify", side_effect=AssertionError("racing automatic trigger")),
    ):
        package_discovery.scan(config, cid, vid)
    # The older scan cannot consume the newer request's readiness token.
    assert package_discovery.status(config, cid, vid)["state"] == "queued"
    assert package_discovery.status(config, cid, vid)["request_id"] == admitted[0]["job_id"]
    drain(config)
    package_discovery.tick(config)
    assert explicit_refresh.ready(config, vid)
    assert not store.rows(config, "triggers", vid)


def test_registered_package_versions_stay_immutable_during_explicit_refresh(config, scope, package):
    cid, vid, root = scope
    package_discovery.scan(config, cid, vid, notify=False)
    metadata(config, vid, root)
    before = store.rows(config, "versions", vid)
    value = json.loads((package / "manifest.json").read_text())
    value["package"]["version"] = "2"
    (package / "manifest.json").write_text(json.dumps(value))
    commands.submit(config, cid, vid, reobserve=True)
    drain(config)
    package_discovery.tick(config)
    assert explicit_refresh.ready(config, vid)
    versions = store.rows(config, "versions", vid)
    assert len(versions) == 2 and all(v in versions for v in before)
