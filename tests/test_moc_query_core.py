"""Synthetic direct-member query checks; no live Vault, model or network."""

from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_moc_associations import moc_scope as moc_scope
from test_moc_files import apply, plan
from test_moc_files import file_scope as file_scope
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope

from fiber_bundle import auth, database, moc_store, note_identity_store, note_index
from fiber_bundle.config import IntegrityError
from fiber_bundle.moc_query_snapshot import MountedQuerySnapshot
from fiber_bundle.organization import store as organization_store


@pytest.fixture
def mounted(config, file_scope):
    apply(config, file_scope, plan(config, file_scope))
    client, vault, root, mid, identity = file_scope
    # Query permission alone is sufficient, even after management permission removal.
    with database.connect(config.state_root) as db:
        auth.set_grant(db, client["id"], vault, query=True, original=False, add=False)
    return client["id"], vault, root, mid, identity


@pytest.mark.parametrize("observed", [False, True])
def test_mount_and_query_former_special_path(config, file_scope, observed):
    import time

    from fiber_bundle import vault_scope
    from fiber_bundle.vault_sync import index, scanner, store

    client, vid, root, _, identity = file_scope
    relative = "01-Projects/protected_project/manual.md"
    target = root / relative
    target.parent.mkdir(parents=True)
    original = (root / "manual.md").read_bytes()
    (root / "manual.md").rename(target)
    record = next(r for r in note_identity_store.records(config, vid) if r.identity == identity)
    note_identity_store.save(
        config,
        vid,
        client["id"],
        replace(record, relative_path=relative, revision=record.revision + 1),
        expected_revision=record.revision,
    )
    if observed:
        with database.connect(config.state_root) as db:
            vault = auth.grant(db, client["id"], vid, "query")
            scope = vault_scope.for_vault(root, vault)
        store.register(config, vid, str(root), scope, "synthetic-binding")
        while tasks := store.due(config, now=10):
            for task in tasks:
                scanner.apply(config, task, now=10)
    packet = plan(config, file_scope)
    assert relative in packet["markdown"]
    assert apply(config, file_scope, packet)["moc_file_write_performed"]
    if observed:
        now = time.time()
        store.enqueue(config, vid, [("", 1)], "20", now=now)
        while tasks := store.due(config, now=now + 60):
            for task in tasks:
                scanner.apply(config, task, now=now + 60)
        scanner.heartbeat(config, vid)
        index.update(config, client["id"], vid)
    with database.connect(config.state_root) as db:
        with MountedQuerySnapshot(config, db, client["id"]) as snapshot:
            vault = auth.grant(db, client["id"], vid, "query")
            assert [n.identity for n in snapshot.inventory(vault).notes] == [identity], str(
                snapshot.coverage(vid)
            )
            assert snapshot.read_note(vid, root, relative).text
            assert snapshot.coverage(vid)["complete"]
    assert target.read_bytes() == original


def test_reads_current_members_once_without_index_or_inventory(config, mounted, monkeypatch):
    cid, vid, root, _, identity = mounted
    (root / "unmounted.md").write_text("# Unmounted\nprivate-body-sentinel\n")
    (root / "manual.md").write_text("# Current member\ncurrent-only-sentinel\n")
    seen = []
    original = note_index.read_note

    def read(root, path, limit, **kwargs):
        seen.append(path)
        return original(root, path, limit, **kwargs)

    monkeypatch.setattr(note_index, "read_note", read)
    with (
        database.connect(config.state_root) as db,
        patch("fiber_bundle.note_inventory.read", side_effect=AssertionError("whole inventory")),
        patch(
            "fiber_bundle.note_search_cache.fingerprint", side_effect=AssertionError("fingerprint")
        ),
        patch(
            "fiber_bundle.vault_sync.index_state.token", side_effect=AssertionError("index token")
        ),
        patch("fiber_bundle.note_placement.guard", side_effect=AssertionError("write guard")),
    ):
        vault = auth.grant(db, cid, vid, "query")
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            inventory, matches, cached = snapshot.candidates(vault, "current-only-sentinel")
            assert [n.identity for _, n in matches] == [identity]
            assert not cached and snapshot.titles(vault) == ["Current member"]
            note = snapshot.read_note(vid, root, "manual.md")
            assert note.sha256 == inventory.notes[0].sha256
            assert snapshot.coverage(vid)["complete"]
            assert snapshot.scope_descriptor()["vaults"][vid]["members"] == [identity]
    assert seen.count("manual.md") == 1
    assert "unmounted.md" not in seen


def test_empty_scope_does_not_read_unmounted_bodies(config, identity_scope, monkeypatch):
    cid, vid = identity_scope[0]["id"], identity_scope[1]
    monkeypatch.setattr(note_index, "read_note", lambda *a, **k: pytest.fail("body read"))
    with database.connect(config.state_root) as db:
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            vault = auth.grant(db, cid, vid, "query")
            assert not snapshot.has_members(vault)
            assert not snapshot.candidates(vault, "sentinel")[1]
            assert snapshot.coverage(vid)["complete"]


def test_missing_member_is_local_and_does_not_hide_healthy_note(config, mounted):
    cid, vid, root, mid, identity = mounted
    missing = "note:" + str(uuid4())
    before = moc_store.load(config, vid, mid)
    moc_store.save(
        config,
        vid,
        mid,
        cid,
        replace(
            before, revision=before.revision + 1, members={**before.members, missing: ["manual"]}
        ),
        "test",
        before.revision,
    )
    with database.connect(config.state_root) as db:
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            vault = auth.grant(db, cid, vid, "query")
            assert [
                n.identity for _, n in snapshot.candidates(vault, "private-body-sentinel")[1]
            ] == [identity]
            coverage = snapshot.coverage(vid)
            assert not coverage["complete"]
            assert any(item["identity"] == missing for item in coverage["unavailable"])
            assert snapshot.candidates(vault, "absent-term")[1] == []


@pytest.mark.parametrize("mutation", ["body", "identity", "unmount", "permission"])
def test_referenced_source_changes_fail_delivery(config, mounted, mutation):
    cid, vid, root, mid, identity = mounted
    with database.connect(config.state_root) as db:
        snapshot = MountedQuerySnapshot(config, db, cid)
        snapshot.__enter__()
        snapshot.read_note(vid, root, "manual.md")
        if mutation == "body":
            (root / "manual.md").write_text("# User edited\nchanged\n")
        elif mutation == "identity":
            (root / "manual.md").unlink()
            (root / "manual.md").write_text("# Replacement\nprivate-body-sentinel\n")
        elif mutation == "unmount":
            before = moc_store.load(config, vid, mid)
            moc_store.save(
                config,
                vid,
                mid,
                cid,
                replace(before, revision=before.revision + 1, members={}),
                "unlink",
                before.revision,
            )
        else:
            auth.set_grant(db, cid, vid, query=False, original=False, add=False)
        with pytest.raises((IntegrityError, auth.Denied)):
            snapshot.__exit__(None, None, None)


def test_frozen_scope_ignores_new_members_but_rejects_removal(config, mounted):
    cid, vid, root, mid, identity = mounted
    with database.connect(config.state_root) as db:
        vault = auth.grant(db, cid, vid, "query")
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            snapshot.read_note(vid, root, "manual.md")
            descriptor = snapshot.scope_descriptor()
            before = moc_store.load(config, vid, mid)
            moc_store.save(
                config,
                vid,
                mid,
                cid,
                replace(
                    before,
                    revision=before.revision + 1,
                    members={**before.members, "note:" + str(uuid4()): ["manual"]},
                ),
                "link",
                before.revision,
            )
        with MountedQuerySnapshot(config, db, cid, frozen_scope=descriptor) as snapshot:
            assert [n.identity for n in snapshot.inventory(vault).notes] == [identity]
            assert snapshot.coverage(vid)["complete"]


def test_backlink_and_confirmed_manual_frontmatter_are_live(config, mounted):
    cid, vid, root, mid, identity = mounted
    # Keep the MOC physical binding while removing managed membership.
    before = moc_store.load(config, vid, mid)
    moc_store.save(
        config,
        vid,
        mid,
        cid,
        replace(before, revision=before.revision + 1, members={}),
        "test",
        before.revision,
    )
    target = root / "00-Maps/topic MOC.md"
    target.write_text(target.read_text() + "\n[[manual]]\n")
    with database.connect(config.state_root) as db:
        vault = auth.grant(db, cid, vid, "query")
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            assert [n.identity for n in snapshot.inventory(vault).notes] == [identity]
    target.write_text(target.read_text().replace("[[manual]]", ""))
    (root / "manual.md").write_text(
        "---\nmoc: '[[00-Maps/topic MOC]]'\n---\n# Human\nmanual-proof\n"
    )
    organization_store.put(
        config,
        "entities",
        vid + ":" + identity,
        vid,
        {"entity": identity, "path": "manual.md", "manual": [mid], "kind": "note"},
    )
    with database.connect(config.state_root) as db:
        vault = auth.grant(db, cid, vid, "query")
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            assert [n.identity for n in snapshot.inventory(vault).notes] == [identity]
            snapshot.read_note(vid, root, "manual.md")
        (root / "manual.md").write_text("# Human\nmanual-proof\n")
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            assert not snapshot.inventory(vault).notes


def test_protected_member_is_denied_before_file_probe(config, mounted, monkeypatch):
    cid, vid, root, mid, identity = mounted
    registered = next(r for r in note_identity_store.records(config, vid) if r.identity == identity)
    blocked = replace(
        registered,
        relative_path="private/secret.md",
        revision=registered.revision + 1,
    )
    note_identity_store.save(config, vid, cid, blocked, expected_revision=registered.revision)
    original = note_index.read_note
    seen = []

    def read(root, path, limit, **kwargs):
        seen.append(path)
        assert "private" not in path
        return original(root, path, limit, **kwargs)

    monkeypatch.setattr(note_index, "read_note", read)
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            ('{"include":[""],"exclude":["private"]}', vid),
        )
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            assert not snapshot.inventory(auth.grant(db, cid, vid, "query")).notes
            assert not snapshot.coverage(vid)["complete"]


def test_unrelated_pending_archive_does_not_block_and_affected_member_isolated(config, mounted):
    from fiber_bundle.inputs import canonical

    cid, vid, root, mid, identity = mounted
    with database.connect(config.state_root) as db:
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,'note_placement','reconcile',?,1,1)",
            (
                "synthetic-op",
                vid,
                canonical(
                    {
                        "identity": "note:unrelated",
                        "moves": {"unrelated.md": "Archive/unrelated.md"},
                    }
                ),
            ),
        )
        vault = auth.grant(db, cid, vid, "query")
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            assert snapshot.inventory(vault).notes
            assert snapshot.coverage(vid)["complete"]
        db.execute(
            "UPDATE vault_operations SET payload=? WHERE id='synthetic-op'",
            (canonical({"identity": identity, "moves": {"manual.md": "Archive/manual.md"}}),),
        )
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            assert not snapshot.inventory(vault).notes
            assert snapshot.coverage(vid)["unavailable"][0]["reason"] == "moc_query_member_pending"


def test_article_metadata_is_limited_to_members_and_bad_unrelated_location_ignored(
    config, policy, article_scope
):
    from test_note_placement import published

    cid, vid = article_scope[:2]
    _, publication, note = published(config, policy, article_scope)
    root = note.parent.parent
    identity = "publication:" + publication["publication_id"]
    mid = str(uuid4())
    info = root.stat()
    moc_store.save(
        config,
        vid,
        mid,
        cid,
        moc_store.Record("Mounted article", info.st_dev, info.st_ino, 1, {identity: ["manual"]}),
        "test",
        0,
    )
    with database.connect(config.state_root) as db:
        db.execute(
            "INSERT INTO publication_locations SELECT kind,?,vault_id,entry_note_path,"
            "package_root,revision,state,? FROM publication_locations LIMIT 1",
            (str(uuid4()), '{"mapping":false}'),
        )
        vault = auth.grant(db, cid, vid, "query")
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            inv = snapshot.inventory(vault)
            assert len(inv.notes) == 1
            current = inv.notes[0]
            assert current.identity == identity
            assert current.publication_id == publication["publication_id"]
            assert set(inv.publications) == {publication["publication_id"]}
            assert current.relative_path in snapshot.managed_entries(vault)
            snapshot.read_note(vid, root, current.relative_path)


def test_expert_members_are_finite_and_removed_members_fail_delivery(config, mounted):
    cid, vid, root, mid, identity = mounted
    package_id, other_id = str(uuid4()), str(uuid4())
    key = root.stat()
    for pid in (package_id, other_id):
        organization_store.put(
            config,
            "packages",
            pid,
            vid,
            {
                "id": pid,
                "module_id": "expert:" + pid,
                "vault_id": vid,
                "path": "Book/" + pid,
                "vault_key": [key.st_dev, key.st_ino],
            },
        )
    before = moc_store.load(config, vid, mid)
    moc_store.save(
        config,
        vid,
        mid,
        cid,
        replace(
            before,
            revision=before.revision + 1,
            members={**before.members, "expert:" + package_id: ["manual"]},
        ),
        "test",
        before.revision,
    )
    with database.connect(config.state_root) as db:
        vault = auth.grant(db, cid, vid, "query")
        snapshot = MountedQuerySnapshot(config, db, cid)
        assert snapshot.expert_ids(vault) == {package_id}
        path = "Book/" + package_id + "/references/knowledge/item.md"
        snapshot.assert_source(vid, path, "expert:" + package_id)
        before = moc_store.load(config, vid, mid)
        moc_store.save(
            config,
            vid,
            mid,
            cid,
            replace(before, revision=before.revision + 1, members={identity: ["system"]}),
            "unlink",
            before.revision,
        )
        with pytest.raises(IntegrityError, match="moc_query_member_removed_or_changed"):
            snapshot.__exit__(None, None, None)


def test_explicit_unregistered_backlink_survives_frozen_phase(config, mounted):
    cid, vid, root, mid, _ = mounted
    (root / "Research").mkdir()
    (root / "Research/human.md").write_text("# Human source\nfinite-backlink-proof\n")
    target = root / "00-Maps/topic MOC.md"
    target.write_text(target.read_text() + "\n[[Research/human]]\n")
    with database.connect(config.state_root) as db:
        vault = auth.grant(db, cid, vid, "query")
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            matches = snapshot.candidates(vault, "finite-backlink-proof")[1]
            assert len(matches) == 1
            expected = matches[0][1].identity
            descriptor = snapshot.scope_descriptor()
            snapshot.read_note(vid, root, "Research/human.md")
        assert descriptor["vaults"][vid]["paths"][expected] == "Research/human.md"
        with MountedQuerySnapshot(config, db, cid, frozen_scope=descriptor) as snapshot:
            assert snapshot.candidates(vault, "finite-backlink-proof")[1][0][1].identity == expected
            snapshot.read_note(vid, root, "Research/human.md")
        target.write_text(target.read_text().replace("[[Research/human]]", ""))
        with MountedQuerySnapshot(config, db, cid, frozen_scope=descriptor) as snapshot:
            assert not snapshot.candidates(vault, "finite-backlink-proof")[1]
            assert any(
                item["identity"] == expected for item in snapshot.coverage(vid)["unavailable"]
            )


def test_cross_vault_claim_only_isolates_its_member(config, mounted):
    from fiber_bundle import note_migration_store
    from fiber_bundle.inputs import canonical

    cid, vid, root, _, identity = mounted
    other = str(uuid4())
    with note_migration_store.connection(config, write=True) as side:
        side.execute(
            "INSERT INTO intents VALUES(?,?,?,?,?,'claimed')",
            (
                "synthetic-claim",
                cid,
                vid,
                other,
                canonical(
                    {
                        "registration": {"identity": "note:" + str(uuid4())},
                        "plan": {"source_path": "unrelated.md", "target_path": "elsewhere.md"},
                    }
                ),
            ),
        )
    with database.connect(config.state_root) as db:
        vault = auth.grant(db, cid, vid, "query")
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            assert snapshot.inventory(vault).notes
        with note_migration_store.connection(config, write=True) as side:
            side.execute(
                "UPDATE intents SET intent_json=?",
                (
                    canonical(
                        {
                            "registration": {"identity": identity},
                            "plan": {"source_path": "manual.md", "target_path": "elsewhere.md"},
                        }
                    ),
                ),
            )
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            assert not snapshot.inventory(vault).notes
            assert snapshot.coverage(vid)["unavailable"][0]["reason"] == "moc_query_member_pending"


@pytest.mark.parametrize("failure", ["read", "location"])
def test_stale_manual_candidate_is_not_a_confirmed_member_gap(config, mounted, failure):
    cid, vid, root, mid, healthy = mounted
    stale = "note:" + str(uuid4())
    path = "Retired/stale-candidate.md"
    if failure == "location":
        existing = next(
            r for r in note_identity_store.records(config, vid) if r.identity == healthy
        )
        note_identity_store.save(
            config,
            vid,
            cid,
            replace(
                existing,
                identity=stale,
                relative_path=path,
                file_key=(existing.file_key[0], existing.file_key[1] + 1, existing.file_key[2]),
                vault_key=(existing.vault_key[0], existing.vault_key[1] + 1),
            ),
        )
    organization_store.put(
        config,
        "entities",
        vid + ":" + stale,
        vid,
        {"entity": stale, "path": path, "manual": [mid], "kind": "note"},
    )
    with database.connect(config.state_root) as db:
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            vault = auth.grant(db, cid, vid, "query")
            matches = snapshot.candidates(vault, "private-body-sentinel")[1]
            assert [note.identity for _, note in matches] == [healthy]
            coverage = snapshot.coverage(vid)
            assert coverage["member_count"] == 1
            assert coverage["unavailable_count"] == 0 and coverage["unavailable"] == []
            assert coverage["candidate_unavailable_count"] == 1
            assert coverage["candidate_unavailable"][0]["identity"] == stale
            assert coverage["candidate_unavailable"][0]["reason"].startswith("moc_query_candidate_")
            assert not coverage["complete"]
            assert stale not in snapshot.scope_descriptor()["vaults"][vid]["members"]


def test_map_navigation_backlinks_do_not_turn_map_pages_into_query_members(
    config, mounted, monkeypatch
):
    cid, vid, root, mid, healthy = mounted
    (root / "Map").mkdir()
    other = root / "Map/Related MOC.md"
    other.write_text("# Related MOC\nmap-navigation-only-token\n[[00-Maps/topic MOC]]\n")
    original_map = root / "00-Maps/topic MOC.md"
    original_map.write_text(
        original_map.read_text()
        + "\nmap-navigation-only-token\n[[Map/Related MOC]]\n[[00-Maps/topic MOC]]\n"
    )
    key = root.stat()
    map_ids = []
    for path in ("Map/Related MOC.md", "00-Maps/topic MOC.md"):
        note = note_index.read_note(root, path, note_index.Limits().note_bytes)
        identity = "note:" + str(uuid4())
        map_ids.append(identity)
        note_identity_store.save(
            config,
            vid,
            cid,
            note_identity_store.Registration(
                identity, None, path, note.sha256, note.file_key, (key.st_dev, key.st_ino), 1
            ),
        )
    # Even an old explicit ledger entry must respect the catalog entity boundary.
    before = moc_store.load(config, vid, mid)
    moc_store.save(
        config,
        vid,
        mid,
        cid,
        replace(
            before, revision=before.revision + 1, members={**before.members, map_ids[0]: ["manual"]}
        ),
        "test",
        before.revision,
    )
    seen = []
    original = note_index.read_note

    def read(root, path, limit, **kwargs):
        seen.append(path)
        return original(root, path, limit, **kwargs)

    monkeypatch.setattr(note_index, "read_note", read)
    with database.connect(config.state_root) as db:
        with MountedQuerySnapshot(config, db, cid) as snapshot:
            vault = auth.grant(db, cid, vid, "query")
            inventory, matches, _ = snapshot.candidates(vault, "map-navigation-only-token")
            assert matches == []
            assert [note.identity for note in inventory.notes] == [healthy]
            assert snapshot.scope_descriptor()["vaults"][vid]["members"] == [healthy]
            assert snapshot.coverage(vid)["complete"]
    assert "00-Maps/topic MOC.md" in seen  # The Map is still read for human relations.
    assert "Map/Related MOC.md" not in seen  # No ordinary-note discovery read of Maps.
