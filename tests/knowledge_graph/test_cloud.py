"""Actual indexed notes, literal links and authorization; cloud reads never write."""

import hashlib
from unittest.mock import patch

import pytest
from agent_connections.test_access_skill import connect
from test_api import add_client, permissions, vault_ids
from test_moc_associations import moc_scope as moc_scope
from test_moc_files import apply, plan
from test_moc_files import file_scope as file_scope
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_vault_sync_reconcile import observe

from fiber_bundle import auth, database, note_index, note_search_build, note_search_cache
from fiber_bundle.knowledge_graph import cloud, cloud_links
from fiber_bundle.vault_sync import index, scanner, store


@pytest.fixture
def library(http, config):
    client, _ = add_client(http)
    vault = vault_ids(http)["personal"]
    permissions(http, client, vault, query=True)
    root = config.project_root / "test vault/temp_vault1"
    (root / "Map").mkdir()
    (root / "alpha.md").write_text("# Alpha 中文\n[[beta]]\n```md\n[[isolated]]\n```\n")
    (root / "beta.md").write_text("# Beta\n[Alpha](alpha.md)\n")
    (root / "isolated.md").write_text("# 孤立笔记\nNo links.\n")
    (root / "Map/topic MOC.md").write_text("---\ntype: moc\n---\n# Topic\n[[alpha]]\n")
    note_search_build.build(config, client, vault)
    return client, vault, root


def snapshot(config, library, **kwargs):
    with database.connect(config.state_root) as db:
        return cloud.read(config, db, *library[:2], **kwargs)


def relation_paths(value):
    paths = {node["id"]: node["path"] for node in value["nodes"]}
    return {
        (frozenset((paths[e["source"]], paths[e["target"]])), e["kind"]) for e in value["edges"]
    }


def test_all_unregistered_notes_isolates_literal_links_and_no_code_edges(config, library):
    with (
        patch("fiber_bundle.note_inventory.read", side_effect=AssertionError("whole body scan")),
        patch.object(note_index, "read_note", side_effect=AssertionError("source body opened")),
    ):
        value = snapshot(config, library)
    assert len(value["nodes"]) == 4
    assert all(n["id"].startswith("index:") and n["note_id"] is None for n in value["nodes"])
    assert relation_paths(value) == {
        (frozenset(("alpha.md", "beta.md")), "explicit"),
        (frozenset(("Map/topic MOC.md", "alpha.md")), "explicit"),
    }
    assert next(n for n in value["nodes"] if n["path"].startswith("Map/"))["kind"] == "moc"
    assert value["coverage"]["complete"] and value["coverage"]["links_complete"]
    assert not (config.state_root / "note-identities.sqlite3").exists()


def test_bounded_display_searches_all_indexed_notes_and_focus_includes_hidden_result(
    config, library
):
    value = snapshot(config, library, max_nodes=1)
    assert len(value["nodes"]) == 1 and not value["edges"]
    assert value["coverage"]["omitted_nodes"] == 3
    assert not value["coverage"]["complete"]
    with database.connect(config.state_root) as db:
        found = cloud.search(config, db, *library[:2], "孤立")
    assert found["items"][0]["path"] == "isolated.md"
    focus = found["items"][0]["id"]
    focused = snapshot(config, library, max_nodes=1, focus=focus)
    assert focused["nodes"][0]["id"] == focus
    with pytest.raises(auth.Denied):
        snapshot(config, library, focus="index:" + "0" * 64)


def test_read_preserves_database_index_ledgers_and_note_bytes(http, config, library):
    files = [
        p
        for p in config.state_root.rglob("*")
        if p.is_file() and not p.name.endswith(("-shm", "-wal"))
    ]
    files += list(library[2].rglob("*.md"))
    before = {
        p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_ino)
        for p in files
    }
    for _ in range(2):
        response = http.get(
            "/admin/knowledge-graph/note-cloud",
            params={"client_id": library[0], "vault_id": library[1]},
        )
        assert response.status_code == 200, response.text
    assert before == {
        p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_ino)
        for p in files
    }


def test_http_requires_admin_and_current_scope_before_opening_cache(http, config, library):
    other, _ = add_client(http, "other")
    params = {"client_id": other, "vault_id": library[1]}
    with patch.object(
        note_search_cache, "connection", side_effect=AssertionError("index before auth")
    ):
        assert http.get("/admin/knowledge-graph/note-cloud", params=params).status_code == 404
        params["query"] = "Alpha"
        assert (
            http.get("/admin/knowledge-graph/note-cloud-search", params=params).status_code == 404
        )
    params["client_id"] = library[0]
    params["vault_id"] = vault_ids(http)["supplier"]
    assert http.get("/admin/knowledge-graph/note-cloud", params=params).status_code == 404
    params["vault_id"] = library[1]
    permissions(http, library[0], library[1], query=False)
    assert http.get("/admin/knowledge-graph/note-cloud", params=params).status_code == 404
    http.cookies.clear()
    assert http.get("/admin/knowledge-graph/note-cloud", params=params).status_code == 401


@pytest.mark.parametrize("state", ["absent", "stale"])
def test_absent_or_stale_cache_does_not_scan_or_queue_rebuild(config, library, state):
    if state == "absent":
        note_search_cache.location(config, library[1]).unlink()
    else:
        (library[2] / "alpha.md").write_text("# Changed\n[[isolated]]\n")
    with (
        patch("fiber_bundle.note_inventory.read", side_effect=AssertionError("body scan")),
        patch(
            "fiber_bundle.note_search_jobs.changed", side_effect=AssertionError("implicit rebuild")
        ),
    ):
        value = snapshot(config, library)
    assert not value["nodes"] and not value["edges"]
    assert value["coverage"]["index_state"] == state
    assert not value["coverage"]["complete"]


def test_current_index_replacement_before_delivery_is_rejected(config, library, monkeypatch):
    actual = cloud_links.read

    def changed(*args, **kwargs):
        result = actual(*args, **kwargs)
        (library[2] / "beta.md").write_text("# Changed outside Fiber\n")
        return result

    monkeypatch.setattr(cloud_links, "read", changed)
    with pytest.raises(note_search_cache.IndexUnavailable):
        snapshot(config, library)


def test_edge_and_text_budgets_are_honest_and_never_return_dangling_edges(
    config, library, monkeypatch
):
    monkeypatch.setattr(cloud, "MAX_EDGES", 1)
    value = snapshot(config, library)
    assert len(value["edges"]) == 1 and value["coverage"]["omitted_edges"] == 1
    monkeypatch.setattr(cloud_links, "MAX_TEXT_BYTES", 1)
    value = snapshot(config, library)
    assert not value["edges"] and not value["coverage"]["links_complete"]
    assert value["coverage"]["link_unavailable_notes"] == 4


def test_cached_line_windows_preserve_long_links_and_fence_context():
    text = "# Note\n" + "a" * 632 + "[[beta]]" + "\n```\n[[hidden]]\n```\n"
    restored = cloud_links._text(
        list(note_index.passages(text)) + list(note_index.adjacent_passages(text))
    )
    assert restored == text.rstrip("\n")
    assert cloud_links._text([(1, "a" * 400), (1, "different")]) is None


def test_confirmed_manual_moc_membership_is_one_real_moc_edge(config, file_scope):
    applied = apply(config, file_scope, plan(config, file_scope))
    assert applied["moc_file_write_performed"]
    note_search_build.build(config, file_scope[0]["id"], file_scope[1])
    with database.connect(config.state_root) as db:
        value = cloud.read(config, db, file_scope[0]["id"], file_scope[1])
    pairs = relation_paths(value)
    assert (frozenset(("manual.md", "00-Maps/topic MOC.md")), "moc") in pairs
    assert all(e["source"] != e["target"] for e in value["edges"])


def test_pending_moc_binding_is_not_committed_membership_evidence(config, file_scope):
    planned = plan(config, file_scope)
    assert planned["status"] == "planned"
    note_search_build.build(config, file_scope[0]["id"], file_scope[1])
    with database.connect(config.state_root) as db:
        value = cloud.read(config, db, file_scope[0]["id"], file_scope[1])
    assert all(edge["kind"] != "moc" for edge in value["edges"])
    assert value["coverage"]["moc_membership_unavailable"] == 1
    assert not value["coverage"]["membership_projection_complete"]
    assert not value["coverage"]["complete"]


@pytest.mark.parametrize("entry", ["directory", "markdown"])
def test_indexed_expert_alias_maps_confirmed_member_or_reports_excluded_entry(
    config, file_scope, entry
):
    from note_cloud_complete_fixture import directory_expert, markdown_expert, save_members

    apply(config, file_scope, plan(config, file_scope))
    client, vault, root, mid, _ = file_scope
    with database.connect(config.state_root) as db:
        auth.set_grant(db, client["id"], vault, query=True, original=True, add=True)
    expert = (
        directory_expert(config, client, vault, root, 99)
        if entry == "directory"
        else markdown_expert(config, client, vault, root)
    )
    save_members(config, client, vault, mid, {expert["id"]: ["manual"]})
    note_search_build.build(config, client["id"], vault)
    with database.connect(config.state_root) as db:
        value = cloud.read(config, db, client["id"], vault)
    nodes = [node for node in value["nodes"] if node["id"] == expert["id"]]
    assert len(nodes) == 1 and nodes[0]["kind"] == "expert"
    assert nodes[0]["path"] == expert["path"]
    assert (frozenset((expert["path"], "00-Maps/topic MOC.md")), "moc") in relation_paths(value)
    assert value["coverage"]["excluded_expert_entries"] == 0
    assert value["coverage"]["moc_membership_unavailable"] == 0
    assert value["coverage"]["unresolved_targets"] == 0


def test_managed_partial_index_omits_changed_source_and_keeps_healthy_isolate(config, library):
    client, vault, root = library
    observe(config, ({"id": client}, vault, root))
    scanner.heartbeat(config, vault)
    index.update(config, client, vault)
    (root / "alpha.md").write_text("# Changed\n[[isolated]]\n")
    with patch.object(note_index, "read_note", side_effect=AssertionError("body read")):
        value = snapshot(config, library)
    assert "alpha.md" not in {n["path"] for n in value["nodes"]}
    assert "isolated.md" in {n["path"] for n in value["nodes"]}
    assert not value["coverage"]["complete"]
    assert value["coverage"]["unavailable_count"] >= 1
    assert not value["edges"]
    with store.connection(config) as side:
        assert side is not None
        assert (
            side.execute("SELECT count(*) FROM pending WHERE vault=?", (vault,)).fetchone()[0] == 0
        )


def test_quality_limited_connection_cannot_read_unverified_note_titles(config, library):
    with database.connect(config.state_root) as db:
        connection = connect(config, db, library[1], "complete")
        value = cloud.read(config, db, connection["client_id"], library[1])
        found = cloud.search(config, db, connection["client_id"], library[1], "Alpha")
    assert not value["nodes"] and not value["edges"] and not found["items"]
    assert value["coverage"]["unavailable"] == [
        {"reason": "quality_scope_requires_verified_projection"}
    ]
    assert not value["coverage"]["complete"]


def test_saved_analysis_fixed_audience_gate_filters_metadata_links_and_search(config, library):
    with (
        patch(
            "fiber_bundle.vault_merge_analysis.records", return_value=[{"note_path": "alpha.md"}]
        ),
        patch("fiber_bundle.document_creation_save.search_source", side_effect=auth.Denied(404)),
    ):
        value = snapshot(config, library)
        with database.connect(config.state_root) as db:
            found = cloud.search(config, db, *library[:2], "Alpha")
    assert "alpha.md" not in {node["path"] for node in value["nodes"]}
    assert not value["edges"] and not found["items"]
