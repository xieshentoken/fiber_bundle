"""Actual disk-index/HTTP behavior, original matching and fail-closed invalidation."""

import hashlib
from unittest.mock import patch

import pytest
from test_api import add_client, permissions, vault_ids
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_search import retrieve

from fiber_bundle import auth, database, note_index, note_search_build, note_search_cache
from fiber_bundle.config import IntegrityError


@pytest.fixture
def indexed_scope(http, config):
    cid, headers = add_client(http)
    vid = vault_ids(http)["personal"]
    permissions(http, cid, vid, query=True, add=True, original=True)
    root = config.project_root / "test vault/temp_vault1"
    (root / "one.md").write_text("# 青松压力\n6 kW MC-IHS 压力为 100 psi。\n型号 AB-12 合格。\n")
    (root / "two.md").write_text("# 压力对照\n青松压力为 80 psi。\n型号 AB-123 待核实。\n")
    (root / "cross.md").write_text("# 记录\n设备压力为\n2.4 MPa\n取值正常。\n")
    return cid, headers, vid, root


@pytest.mark.parametrize(
    "query",
    [
        "青松",
        "压",
        "压力",
        "AB-12",
        "ＭＣ－ＩＨＳ",
        "6 kW",
        "设备 2.4 MPa",
        "2.4 MPa",
        "unknown",
        "AB-12 OR AB-123",
    ],
)
def test_literal_results_identical_to_snapshot(http, config, indexed_scope, query):
    cid, headers, vid, root = indexed_scope
    before = retrieve(http, headers, query, [vid]).json()
    assert note_search_build.build(config, cid, vid)["state"] == "ready"
    after = retrieve(http, headers, query, [vid])
    assert after.status_code == 200, after.text
    assert after.json()["results"] == before["results"]
    assert after.json()["index_mode"] == "persistent_chunks"
    # The existing whole-library body scanner cannot be used by an indexed query.
    with patch("fiber_bundle.note_inventory.read", side_effect=AssertionError("whole scan")):
        assert retrieve(http, headers, query, [vid]).json() == after.json()


@pytest.mark.parametrize("change", ["edit", "new", "delete", "move"])
def test_external_change_requires_explicit_rebuild(http, config, indexed_scope, change):
    cid, headers, vid, root = indexed_scope
    note_search_build.build(config, cid, vid)
    p = root / "one.md"
    if change == "edit":
        p.write_text("# 新记录\nreplacement XY-78\n")
    elif change == "new":
        (root / "new.md").write_text("# 新记录\nreplacement XY-78\n")
    elif change == "delete":
        p.unlink()
    else:
        p.rename(root / "moved.md")
    response = retrieve(http, headers, "AB-12", [vid])
    assert response.status_code == 409 and response.json()["error"] == "note_index_update_required"
    assert "snippet" not in response.text
    assert note_search_build.build(config, cid, vid)["state"] == "ready"
    assert retrieve(http, headers, "AB-12", [vid]).status_code == 200


def test_unauthorized_reads_or_build_never_open_index(http, config, indexed_scope):
    cid, headers, vid, root = indexed_scope
    note_search_build.build(config, cid, vid)
    other, other_headers = add_client(http, "other")
    with patch.object(
        note_search_cache, "connection", side_effect=AssertionError("index read before auth")
    ):
        assert retrieve(http, other_headers, "青松", [vid]).status_code == 404
    with pytest.raises(auth.Denied):
        note_search_build.build(config, other, vid)
    permissions(http, cid, vid, query=False)
    assert retrieve(http, headers, "青松", [vid]).status_code == 404


def test_failed_rebuild_preserves_old_generation_and_owned_staging_only(
    http, config, indexed_scope
):
    cid, headers, vid, root = indexed_scope
    note_search_build.build(config, cid, vid)
    path = note_search_cache.location(config, vid)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    # A changed source must be read; unchanged sources may legitimately be reused.
    (root / "new.md").write_text("# new source\nrequires read\n")
    with patch.object(note_index, "read_note", side_effect=IntegrityError("source unavailable")):
        with pytest.raises(IntegrityError):
            note_search_build.build(config, cid, vid)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert list(path.parent.glob("*.sqlite3")) == [path]
    assert retrieve(http, headers, "青松", [vid]).status_code == 409
    (root / "new.md").unlink()
    note_search_build.build(config, cid, vid)
    assert retrieve(http, headers, "青松", [vid]).status_code == 200


def test_build_releases_storage_lease_and_rechecks_regrant(http, config, indexed_scope):
    cid, headers, vid, root = indexed_scope
    original = note_index.read_note
    changed = False

    def read(*args, **kwargs):
        nonlocal changed
        if not changed:
            # Acquiring the storage lock here proves body I/O does not hold it.
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    auth.set_grant(db, cid, vid, query=False, original=True, add=True)
                    auth.set_grant(db, cid, vid, query=True, original=True, add=True)
                finally:
                    db.close()
            changed = True
        return original(*args, **kwargs)

    with patch.object(note_index, "read_note", side_effect=read):
        with pytest.raises(IntegrityError, match="permission changed"):
            note_search_build.build(config, cid, vid)
    assert not note_search_cache.location(config, vid).exists()


def test_indexed_query_reads_only_delivered_notes(http, config, indexed_scope):
    cid, headers, vid, root = indexed_scope
    note_search_build.build(config, cid, vid)
    with patch.object(note_index, "read_note", wraps=note_index.read_note) as reads:
        result = retrieve(http, headers, "AB-12", [vid])
    assert result.status_code == 200 and len(result.json()["results"]) == 1
    assert reads.call_count == 1 and reads.call_args.args[1] == "one.md"


def test_library_beyond_old_32_mib_is_streamed_and_searchable(http, config, indexed_scope):
    cid, headers, vid, root = indexed_scope
    # Each note fits the unchanged per-note limit. Aggregate exceeds the old snapshot cap.
    for i in range(34):
        (root / f"chapter-{i:02d}.md").write_text(
            f"# 第{i}章\nunique-chapter-{i:02d} evidence\n" + "x" * 999_950 + "\n"
        )
    assert sum(p.stat().st_size for p in root.glob("*.md")) > 32 * 1024 * 1024
    observed = []
    original = note_index.snapshot

    def snapshot(*args, **kwargs):
        value = original(*args, **kwargs)
        assert all(n.text == "" for n in value)
        observed.append(len(value))
        return value

    with patch.object(note_index, "snapshot", side_effect=snapshot):
        built = note_search_build.build(config, cid, vid)
    assert built["notes"] == 37 and observed == [37]
    with patch("fiber_bundle.note_inventory.read", side_effect=AssertionError("whole scan")):
        for chapter in (0, 17, 33):
            response = retrieve(http, headers, f"unique-chapter-{chapter:02d}", [vid])
            assert response.status_code == 200, response.text
            assert response.json()["results"][0]["note_path"] == f"chapter-{chapter:02d}.md"


def test_status_is_current_scoped_and_restored_generation_is_stale(
    http, config, indexed_scope, tmp_path
):
    from fiber_bundle import backup_bundle, backup_preflight, backup_restore
    from fiber_bundle.config import load_config

    cid, headers, vid, root = indexed_scope
    route = "/v1/vaults/" + vid + "/note-index"
    assert http.get(route, headers=headers).json()["state"] == "absent"
    note_search_build.build(config, cid, vid)
    assert http.get(route, headers=headers).json()["state"] == "ready"
    assert http.get(route).status_code == 401
    preflight = backup_preflight.inspect(config)
    assert {"path": "note-search/" + vid + ".sqlite3", "schema": 1} in preflight["databases"]
    backup_bundle.create(config, tmp_path / "bundle", "synthetic-backup-password")
    backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", "synthetic-backup-password")
    target = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    backup_restore.activate(target, source_stopped=True)
    with note_search_cache.connection(target, vid) as cache:
        assert cache.execute("SELECT state FROM generation").fetchone()[0] == "stale"
        assert cache.execute("SELECT count(*) FROM notes").fetchone()[0] == 3
    assert (target.project_root / "test vault/temp_vault1/one.md").read_bytes() == (
        root / "one.md"
    ).read_bytes()


def test_registered_identity_survives_cache_and_move_rebuild(http, config, indexed_scope):
    from fiber_bundle import note_identities

    cid, headers, vid, root = indexed_scope
    tracked = note_identities.run(
        config,
        headers["Authorization"],
        vid,
        "track",
        "one.md",
        hashlib.sha256((root / "one.md").read_bytes()).hexdigest(),
    )
    note_search_build.build(config, cid, vid)
    hit = retrieve(http, headers, "AB-12", [vid]).json()["results"][0]
    assert hit["note_id"] == tracked["note_identity"]
    (root / "one.md").rename(root / "moved.md")
    note_search_build.build(config, cid, vid)
    hit = retrieve(http, headers, "AB-12", [vid]).json()["results"][0]
    assert hit["note_path"] == "moved.md" and hit["note_id"] == tracked["note_identity"]


def test_candidate_limit_is_explicit_without_partial_results(
    http, config, indexed_scope, monkeypatch
):
    cid, headers, vid, root = indexed_scope
    note_search_build.build(config, cid, vid)
    monkeypatch.setattr(note_search_cache, "MAX_MATCHES", 1)
    result = retrieve(http, headers, "压力", [vid])
    assert result.status_code == 409 and result.json()["error"] == "note_index_match_limit"
    assert "snippet" not in result.text


def test_index_hit_changed_before_delivery_rejected(http, config, indexed_scope):
    cid, headers, vid, root = indexed_scope
    note_search_build.build(config, cid, vid)
    original = note_index.read_note

    def changed(*args, **kwargs):
        (root / "one.md").write_text("replacement evidence")
        return original(*args, **kwargs)

    with patch.object(note_index, "read_note", side_effect=changed):
        result = retrieve(http, headers, "AB-12", [vid])
    assert result.status_code == 503 and "snippet" not in result.text


def test_abandoned_build_does_not_block_backup_or_become_live(http, config, indexed_scope):
    from uuid import uuid4

    from fiber_bundle import backup_preflight

    cid, headers, vid, root = indexed_scope
    note_search_build.build(config, cid, vid)
    abandoned = note_search_cache.location(config, vid).parent / (str(uuid4()) + ".building")
    abandoned.write_bytes(b"")
    before = abandoned.stat().st_ino
    result = backup_preflight.inspect(config)
    assert result["state"] == "PASS"
    assert any(e["path"] == "note-search/" + abandoned.name for e in result["entries"])
    assert abandoned.stat().st_ino == before
    assert retrieve(http, headers, "AB-12", [vid]).status_code == 200


def test_published_article_cache_preserves_provenance_and_original_grants(
    config, policy, article_scope
):
    from test_article_publication import completed

    from fiber_bundle import search
    from fiber_bundle.article_publication import publish

    rid = completed(config, policy, article_scope)
    publish(config, article_scope[0], rid)
    cid, vid, source = article_scope
    db = database.connect(config.state_root)
    auth.set_grant(db, cid, vid, query=True, original=False, add=True)
    before = search.search(config, db, cid, "Synthetic", [vid])["results"]
    note_search_build.build(config, cid, vid)
    after = search.search(config, db, cid, "Synthetic", [vid])["results"]
    assert after == before and after[0]["source_kind"] == "published_article"
    assert after[0]["originals"] == []
    auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    assert search.search(config, db, cid, "Synthetic", [vid])["results"][0]["originals"]
    db.close()


def test_incremental_reuses_only_unchanged_bodies_and_deletes_stale_chunks(
    http, config, indexed_scope
):
    cid, headers, vid, root = indexed_scope
    first = note_search_build.build(config, cid, vid)
    assert first["updated_notes"] == 3 and first["reused_notes"] == 0
    with patch.object(note_index, "read_note", side_effect=AssertionError("unchanged body read")):
        second = note_search_build.build(config, cid, vid)
    assert second["updated_notes"] == 0 and second["reused_notes"] == 3
    (root / "two.md").unlink()
    (root / "one.md").write_text("# replacement\nnew-token-replacement\n")
    with patch.object(note_index, "read_note", wraps=note_index.read_note) as reads:
        third = note_search_build.build(config, cid, vid)
    assert reads.call_count == 1 and reads.call_args.args[1] == "one.md"
    assert third["updated_notes"] == 1 and third["reused_notes"] == 1
    assert not retrieve(http, headers, "AB-123", [vid]).json()["results"]
    assert retrieve(http, headers, "new-token-replacement", [vid]).json()["results"]


def test_cached_graph_inventory_reads_only_selected_current_bodies(http, config, indexed_scope):
    from fiber_bundle import note_search_inventory

    cid, headers, vid, root = indexed_scope
    note_search_build.build(config, cid, vid)
    db = database.connect(config.state_root)
    vault = auth.grant(db, cid, vid, "query")
    metadata = note_search_inventory.read(config, db, vault, selected=set())
    assert len(metadata.notes) == 3 and all(n.text == "" for n in metadata.notes)
    # Explicitly registered identity gives graph selection a stable source key.
    from fiber_bundle import note_identities

    note_identities.run(
        config,
        headers["Authorization"],
        vid,
        "track",
        "one.md",
        hashlib.sha256((root / "one.md").read_bytes()).hexdigest(),
    )
    db.close()
    note_search_build.build(config, cid, vid)
    db = database.connect(config.state_root)
    metadata = note_search_inventory.read(config, db, vault, selected=set())
    identity = next(n.identity for n in metadata.notes if n.relative_path == "one.md")
    with patch.object(note_index, "read_note", wraps=note_index.read_note) as reads:
        current = note_search_inventory.read(config, db, vault, selected={identity})
    assert reads.call_count == 1 and reads.call_args.args[1] == "one.md"
    assert next(n.text for n in current.notes if n.identity == identity)
    (root / "one.md").write_text("changed\n")
    with pytest.raises(note_search_cache.IndexUnavailable):
        note_search_inventory.read(config, db, vault, selected={identity})
    db.close()


def test_admin_explicit_update_and_persistent_zero_model_job(http, config, indexed_scope):
    from fiber_bundle import note_search_jobs

    cid, headers, vid, root = indexed_scope
    scope = {"client_id": cid, "vault_id": vid}
    state = http.get("/admin/note-index/status", params=scope)
    assert state.status_code == 200 and state.json()["state"] == "absent"
    rejected = http.post("/admin/note-index/update", json=scope, headers={"X-CSRF-Token": "wrong"})
    assert rejected.status_code == 403
    updated = http.post("/admin/note-index/update", json=scope)
    assert updated.status_code == 200 and updated.json()["updated_notes"] == 3
    assert note_search_jobs.read(config, vid)["state"] == "ready"
    state = http.get("/admin/note-index/status", params=scope).json()
    assert state["state"] == "ready" and state["update"]["model_calls"] == 0
    (root / "one.md").write_text("# changed\nonly-updated-evidence\n")
    assert http.get("/admin/note-index/status", params=scope).json()["state"] == "stale"
    updated = http.post("/admin/note-index/update", json=scope).json()
    assert updated["updated_notes"] == 1 and updated["reused_notes"] == 2
    permissions(http, cid, vid, query=False)
    assert http.post("/admin/note-index/update", json=scope).status_code == 404


def test_registration_uses_index_metadata_beyond_old_library_cap(http, config, indexed_scope):
    from fiber_bundle import note_identities

    cid, headers, vid, root = indexed_scope
    for i in range(34):
        (root / f"scale-{i}.md").write_text("# Synthetic scale\n" + "x" * 999_950 + "\n")
    note_search_build.build(config, cid, vid)
    with patch("fiber_bundle.note_inventory.read", side_effect=AssertionError("whole scan")):
        tracked = note_identities.run(
            config,
            headers["Authorization"],
            vid,
            "track",
            "one.md",
            hashlib.sha256((root / "one.md").read_bytes()).hexdigest(),
        )
    assert tracked["note_identity"].startswith("note:")
    note_search_build.build(config, cid, vid)
    assert (
        retrieve(http, headers, "AB-12", [vid]).json()["results"][0]["note_id"]
        == tracked["note_identity"]
    )
