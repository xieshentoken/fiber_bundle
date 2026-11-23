"""Observed libraries expose only unchanged versions in an atomically published index."""

import json
import os
import sqlite3
import time
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_channel_moc_query import project_moc
from test_expert_placement import publish_fixture

from fiber_bundle import auth, database, expert_query, indexed_sources, note_search_cache, search
from fiber_bundle.config import InvalidInput
from fiber_bundle.moc_query_snapshot import MountedQuerySnapshot
from fiber_bundle.objects import digest
from fiber_bundle.organization import packages
from fiber_bundle.vault_scope import Scope
from fiber_bundle.vault_sync import index, scanner, store


@pytest.fixture
def mounted_indexed(config, scope):
    from fiber_bundle import moc_associations, note_identities

    _, vid, root = scope
    target = root / "manual.md"
    target.write_text("# Existing member\nprivate-body-sentinel")
    with database.connect(config.state_root) as db:
        client = auth.create_client(db, "indexed MOC owner", False)
        auth.set_grant(db, client["id"], vid, query=True, original=False, add=True)
    token = "Bearer " + client["token"]
    tracked = note_identities.run(
        config, token, vid, "track", target.name, digest(target.read_bytes())
    )
    mid = str(uuid4())
    moc_associations.run(config, token, vid, "create", mid, title="Existing", revision=0)
    moc_associations.run(
        config,
        token,
        vid,
        "link",
        mid,
        revision=1,
        owner="system",
        note_identity=tracked["note_identity"],
        expected_sha256=digest(target.read_bytes()),
    )
    project_moc(config, (client, vid, root, mid, tracked["note_identity"]), 2)
    return client["id"], vid, root, mid, tracked["note_identity"]


def observe(config, cid, vid, root):
    if store.library(config, vid) is None:
        store.register(config, vid, str(root), Scope(), "synthetic")
    store.enqueue(config, vid, [("", 1)], "1", now=1)
    while tasks := store.due(config, now=time.time() + 100):
        for task in tasks:
            scanner.apply(config, task)
    scanner.heartbeat(config, vid)


def query(config, cid, vid, term):
    with database.connect(config.state_root) as db:
        return search.vault_search(config, db, cid, term, [vid])


def test_unobserved_edits_and_new_files_do_not_hide_old_indexed_note(config, scope):
    cid, vid, root = scope
    (root / "stable.md").write_text("# Stable\nindexed-stable-sentinel")
    (root / "edited.md").write_text("# Edited\nold-edited-sentinel")
    observe(config, cid, vid, root)
    index.update(config, cid, vid)
    (root / "edited.md").write_text("# Edited\nnew-edited-sentinel")
    (root / "new.md").write_text("# New\nnew-file-sentinel")
    with patch("fiber_bundle.note_inventory.read", side_effect=AssertionError("whole fallback")):
        assert [
            r["note_path"] for r in query(config, cid, vid, "indexed-stable-sentinel")["results"]
        ] == ["stable.md"]
        assert not query(config, cid, vid, "old-edited-sentinel")["results"]
        assert not query(config, cid, vid, "new-edited-sentinel")["results"]
        assert not query(config, cid, vid, "new-file-sentinel")["results"]
    observe(config, cid, vid, root)
    index.update(config, cid, vid)
    assert query(config, cid, vid, "new-edited-sentinel")["results"]
    assert query(config, cid, vid, "new-file-sentinel")["results"]


def test_excluded_chunks_do_not_exhaust_healthy_match_budget(config, scope):
    cid, vid, root = scope
    (root / "stable.md").write_text("# Stable\nshared-match-sentinel")
    for number in range(12):
        (root / f"edited-{number}.md").write_text("# Edited\nshared-match-sentinel")
    observe(config, cid, vid, root)
    index.update(config, cid, vid)
    for number in range(12):
        (root / f"edited-{number}.md").write_text("# Changed\nunpublished-source")
    with patch("fiber_bundle.note_search_cache.MAX_MATCHES", 10):
        result = query(config, cid, vid, "shared-match-sentinel")
    assert [r["note_path"] for r in result["results"]] == ["stable.md"]


def test_registration_is_not_index_publication_and_unrelated_package_does_not_evict_notes(
    config, scope, package
):
    cid, vid, root = scope
    (root / "stable.md").write_text("# Stable\nindex-stable")
    observe(config, cid, vid, root)
    index.update(config, cid, vid)
    catalog_path = package / "references/runtime/catalog.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["objects"] = [
        {"id": "object", "title": "thermal", "statement": "thermal-indexed-sentinel"}
    ]
    catalog_path.write_text(json.dumps(catalog))
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["integrity"]["files"]["references/runtime/catalog.json"] = digest(
        catalog_path.read_bytes()
    )
    manifest_path.write_text(json.dumps(manifest))
    with database.connect(config.state_root) as db:
        registered = packages.register(config, db, cid, vid, "Book/fake-thermal")
    assert query(config, cid, vid, "index-stable")["results"]
    assert not query(config, cid, vid, "thermal-indexed-sentinel")["results"]
    observe(config, cid, vid, root)
    index.update(config, cid, vid)
    assert (
        query(config, cid, vid, "thermal-indexed-sentinel")["results"][0]["expert_module_version"]
        == registered["version"]
    )
    with note_search_cache.connection(config, vid) as cached:
        assert (
            dict(cached.execute("SELECT id,version FROM indexed_experts"))[registered["id"]]
            == registered["version"]
        )
    # A valid newly registered version cannot use its old indexed text snapshot.
    manifest["package"]["version"] = "2"
    manifest_path.write_text(json.dumps(manifest))
    with database.connect(config.state_root) as db:
        changed = packages.register(config, db, cid, vid, "Book/fake-thermal")
        assert changed["version"] != registered["version"]
        with pytest.raises(InvalidInput, match="not_indexed"):
            expert_query.query(
                config, db, cid, registered["module_id"], registered["version"], "thermal", 1
            )
    assert not query(config, cid, vid, "thermal-indexed-sentinel")["results"]
    assert query(config, cid, vid, "index-stable")["results"]


def test_moc_membership_intersects_published_index_without_global_freshness(
    config, mounted_indexed
):
    cid, vid, root, _, _ = mounted_indexed
    observe(config, cid, vid, root)
    with database.connect(config.state_root) as db:
        vault = auth.grant(db, cid, vid, "query")
        with MountedQuerySnapshot(config, db, cid) as snap:
            assert not snap.has_members(vault)
    index.update(config, cid, vid)
    (root / "new.md").write_text("# Unmounted\nprivate-body-sentinel")
    store.enqueue(config, vid, [("", 1)], "10")
    with database.connect(config.state_root) as db:
        vault = auth.grant(db, cid, vid, "query")
        with MountedQuerySnapshot(config, db, cid) as snap:
            assert [
                n.relative_path for _, n in snap.candidates(vault, "private-body-sentinel")[1]
            ] == ["manual.md"]
    (root / "manual.md").write_text("# Edited member\nnew-unindexed-member-sentinel")
    with database.connect(config.state_root) as db:
        vault = auth.grant(db, cid, vid, "query")
        with MountedQuerySnapshot(config, db, cid) as snap:
            assert not snap.candidates(vault, "new-unindexed-member-sentinel")[1]
            assert snap.coverage(vid)["complete"] is False


def test_legacy_expert_proof_absence_is_local_until_update(config, scope, package):
    cid, vid, root = scope
    with database.connect(config.state_root) as db:
        packages.register(config, db, cid, vid, "Book/fake-thermal")
    (root / "stable.md").write_text("stable-legacy-sentinel")
    observe(config, cid, vid, root)
    index.update(config, cid, vid)
    with sqlite3.connect(note_search_cache.location(config, vid)) as cached:
        cached.execute("DROP TABLE indexed_experts")
    with database.connect(config.state_root) as db:
        current = indexed_sources.read(config, db, cid, auth.grant(db, cid, vid, "query"))
        assert not current.experts and current.coverage["complete"] is False
    assert query(config, cid, vid, "stable-legacy-sentinel")["results"]


def test_ctime_drift_and_unrelated_index_publication_preserve_phase(config, scope):
    from fiber_bundle.search_snapshot import SearchSnapshot

    cid, vid, root = scope
    stable = root / "stable.md"
    stable.write_text("# Stable\nphase-stable-sentinel")
    observe(config, cid, vid, root)
    index.update(config, cid, vid)
    before = stable.stat()
    os.utime(stable, ns=(before.st_atime_ns, before.st_mtime_ns))
    with database.connect(config.state_root) as db, SearchSnapshot(config, db, cid) as snap:
        vault = auth.grant(db, cid, vid, "query")
        assert snap.titles(vault) == ["Stable"]
        (root / "new.md").write_text("# New\nnew-phase-sentinel")
        observe(config, cid, vid, root)
        index.update(config, cid, vid)
        assert search.vault_search(config, db, cid, "phase-stable-sentinel", [vid], snapshot=snap)[
            "results"
        ]
        assert not search.vault_search(config, db, cid, "new-phase-sentinel", [vid], snapshot=snap)[
            "results"
        ]
    assert query(config, cid, vid, "new-phase-sentinel")["results"]


def test_failed_package_scan_suppresses_old_registry_proof_locally(config, scope, package):
    from fiber_bundle import published_expert

    cid, vid, root = scope
    (root / "stable.md").write_text("stable-package-failure-sentinel")
    with database.connect(config.state_root) as db:
        registered = packages.register(config, db, cid, vid, "Book/fake-thermal")
    observe(config, cid, vid, root)
    index.update(config, cid, vid)
    failure = [{"path": "Book/fake-thermal", "reason": "expert_package_scan_failed"}]
    with patch("fiber_bundle.vault_sync.explicit_refresh.package_issues", return_value=failure):
        with database.connect(config.state_root) as db:
            module = published_expert.text(config, db, cid, registered["id"])
            assert not indexed_sources.expert_allowed(config, db, cid, module)
            assert not indexed_sources.capture_experts(config, db, cid, vid)
            view = indexed_sources.read(config, db, cid, auth.grant(db, cid, vid, "query"))
            assert not view.experts and not view.coverage["complete"]
        assert query(config, cid, vid, "stable-package-failure-sentinel")["results"]
        index.update(config, cid, vid)
        with note_search_cache.connection(config, vid) as cached:
            assert cached.execute("SELECT count(*) FROM indexed_experts").fetchone()[0] == 0


def test_expert_phase_cannot_upgrade_versions_or_survive_revocation(config, scope, package):
    from fiber_bundle.search_snapshot import SearchSnapshot

    cid, vid, root = scope
    catalog_path = package / packages.CATALOG
    catalog = json.loads(catalog_path.read_text())
    catalog["objects"] = [{"id": "thermal", "statement": "thermal-phase-sentinel"}]
    catalog_path.write_text(json.dumps(catalog))
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["integrity"]["files"][packages.CATALOG] = digest(catalog_path.read_bytes())
    manifest_path.write_text(json.dumps(manifest))
    with database.connect(config.state_root) as db:
        first = packages.register(config, db, cid, vid, "Book/fake-thermal")
    observe(config, cid, vid, root)
    index.update(config, cid, vid)
    with database.connect(config.state_root) as db, SearchSnapshot(config, db, cid) as snap:
        vault = auth.grant(db, cid, vid, "query")
        assert snap.expert_ids(vault) == {first["id"]}
        manifest["package"]["version"] = "2"
        manifest_path.write_text(json.dumps(manifest))
        second = packages.register(config, db, cid, vid, "Book/fake-thermal")
        assert second["version"] != first["version"]
        observe(config, cid, vid, root)
        index.update(config, cid, vid)
        assert not search.vault_search(
            config, db, cid, "thermal-phase-sentinel", [vid], snapshot=snap
        )["results"]
    with pytest.raises(auth.Denied):
        with database.connect(config.state_root) as db, SearchSnapshot(config, db, cid) as snap:
            assert search.vault_search(
                config, db, cid, "thermal-phase-sentinel", [vid], snapshot=snap
            )["results"]
            auth.set_grant(db, cid, vid, query=False, original=False, add=False)


def test_cloud_pending_never_delivers_cached_body_and_healthy_source_survives(config, scope):
    from fiber_bundle import cloud_files
    from fiber_bundle.cloud_observation import ObservationError

    cid, vid, root = scope
    blocked = root / "blocked.md"
    blocked.write_text("# Blocked\ncloud-shared-sentinel")
    (root / "stable.md").write_text("# Stable\ncloud-shared-sentinel")
    observe(config, cid, vid, root)
    index.update(config, cid, vid)
    original = scanner.signature
    blocked_ino = blocked.stat().st_ino

    def pending_flags(info):
        value = json.loads(original(info))
        if info.st_ino == blocked_ino:
            value[8] |= cloud_files.SF_DATALESS
        return json.dumps(value)

    with patch("fiber_bundle.indexed_sources.signature", side_effect=pending_flags):
        result = query(config, cid, vid, "cloud-shared-sentinel")
        assert [r["note_path"] for r in result["results"]] == ["stable.md"]
        assert not result["coverage_complete"]
    available = cloud_files.require_available

    def refuse_pending(path, **kwargs):
        if path == blocked:
            raise ObservationError("cloud_item_not_downloaded", path, "pending")
        return available(path, **kwargs)

    with patch("fiber_bundle.cloud_files.require_available", side_effect=refuse_pending):
        result = query(config, cid, vid, "cloud-shared-sentinel")
        assert [r["note_path"] for r in result["results"]] == ["stable.md"]
        assert not result["coverage_complete"]


def test_native_published_draft_is_indexed_before_expert_membership_query(
    config, scope, package, mounted_indexed
):
    from dataclasses import replace

    from test_publication_layout import activate

    from fiber_bundle import moc_store, published_expert
    from fiber_bundle.vault_save_contract import settings_wire, unified_settings

    cid, vid, root, mid, _ = mounted_indexed
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    settings = settings_wire(unified_settings())
    settings.pop("layout_mode")
    activate(config, vid, **settings)
    catalog = json.loads((package / "references/runtime/catalog.json").read_text())
    catalog["objects"] = [
        {"id": "native", "title": "native", "statement": "native-indexed-sentinel"}
    ]
    canonical = {
        "manifest.json": b'{"package":{"status":"draft"}}',
        "references/knowledge/index.json": b'{"objects":[]}',
        "references/evidence/anchors.jsonl": b"",
        "references/runtime/catalog.json": json.dumps(catalog).encode(),
        "scripts/query_reference.py": b"# synthetic, never execute\n",
    }
    files = {"note.md": b"# Native Draft\nnative-indexed-sentinel\n"} | {
        ".fiber-expert-canonical/" + name: raw for name, raw in canonical.items()
    }
    identity, location, _ = publish_fixture(config, cid, vid, files)
    with database.connect(config.state_root) as db:
        row = db.execute("SELECT plan_json FROM source_imports WHERE id=?", (identity,)).fetchone()
        plan = json.loads(row[0])
        plan.setdefault("policy", {})["expert_runtime"] = {}
        db.execute("UPDATE source_imports SET plan_json=? WHERE id=?", (json.dumps(plan), identity))
        db.execute(
            "UPDATE source_expert_publications SET manifest_json=? WHERE task_id=?",
            (
                json.dumps(
                    {
                        "files": {
                            name: {"sha256": digest(raw), "byte_count": len(raw)}
                            for name, raw in files.items()
                        }
                    }
                ),
                identity,
            ),
        )
        module = published_expert.text(config, db, cid, identity)
    before = moc_store.load(config, vid, mid)
    moc_store.save(
        config,
        vid,
        mid,
        cid,
        replace(
            before,
            revision=before.revision + 1,
            members={**before.members, "expert:" + identity: ["manual"]},
        ),
        "synthetic",
        before.revision,
    )
    observe(config, cid, vid, root)
    with database.connect(config.state_root) as db:
        with pytest.raises(InvalidInput, match="not_indexed"):
            expert_query.query(
                config, db, cid, "expert:" + identity, module["version"], "native", 1
            )
    index.update(config, cid, vid)
    with database.connect(config.state_root) as db:
        assert indexed_sources.expert_allowed(config, db, cid, module)
        with MountedQuerySnapshot(config, db, cid) as snap:
            assert identity in snap.expert_ids(auth.grant(db, cid, vid, "query"))
            result = search.vault_search(
                config, db, cid, "native-indexed-sentinel", [vid], snapshot=snap
            )
            assert [r["expert_module_id"] for r in result["results"]] == ["expert:" + identity]
        from agent_connections.test_access_skill import connect

        from fiber_bundle.agent_connections import evidence, evidence_verification
        from fiber_bundle.agent_connections import store as connections

        reader = connect(config, db, vid, "all")
        found = search.vault_search(
            config, db, reader["client_id"], "native-indexed-sentinel", [vid]
        )
        sources = evidence.project(found, (vid,), 10)
        assert evidence_verification.verify(
            config, db, reader["client_id"], connections.context(config, db, reader["id"]), sources
        ) == {"verified": True}
