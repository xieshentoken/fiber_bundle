"""Real package/association metadata: complete v2 entities, privacy and freshness."""

import json
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import pytest
from agent_connections.test_access_skill import connect
from note_cloud_complete_fixture import small_cases
from test_api import add_client, permissions, vault_ids

from fiber_bundle import auth, database, note_index, note_search_build, note_search_cache
from fiber_bundle.config import IntegrityError
from fiber_bundle.knowledge_graph import (
    cloud,
    cloud_entities,
    cloud_experts,
    cloud_ledger,
    cloud_sources,
)
from fiber_bundle.organization import store


@pytest.fixture
def complete_library(http, config):
    cid, headers = add_client(http)
    vault = vault_ids(http)["personal"]
    permissions(http, cid, vault, query=True, add=True, original=True)
    client = {"id": cid, "token": headers["Authorization"].removeprefix("Bearer ")}
    root = config.project_root / "test vault/temp_vault1"
    for path in [
        "材料/界面粘附.md",
        "材料/表面能.md",
        "材料/聚合物.md",
        "材料/接触角.md",
        "材料/复合材料.md",
        "方法/不确定度.md",
    ]:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# " + target.stem + "\nSynthetic source.\n")
    fixture = small_cases(config, client, vault, root)
    note_search_build.build(config, cid, vault)
    return client, vault, root, fixture


def read(config, complete_library, **kwargs):
    client, vault, *_ = complete_library
    with database.connect(config.state_root) as db:
        return cloud.read(config, db, client["id"], vault, **kwargs)


def test_complete_entities_and_saved_ownership_without_opening_source(config, complete_library):
    with (
        patch.object(note_index, "read_note", side_effect=AssertionError("source read")),
        patch(
            "fiber_bundle.organization.packages.load", side_effect=AssertionError("package body")
        ),
    ):
        value = read(config, complete_library)
    coverage = value["coverage"]
    assert value["schema"] == "fiber-note-cloud/v2"
    assert coverage["current_notes"] == 9 and coverage["current_experts"] == 2
    assert coverage["unresolved_targets"] == 2 and coverage["membership_edges"] == 14
    assert coverage["complete"] and not coverage["targets_resolved"] and coverage["has_unresolved"]
    assert coverage["manual_memberships"] == 11 and coverage["system_memberships"] == 4
    assert coverage["manual_locked_memberships"] == 1
    assert len({edge["id"] for edge in value["edges"]}) == len(value["edges"])
    native = complete_library[3]["experts"][1]
    assert len([node for node in value["nodes"] if node["path"] == native["path"]]) == 1
    ghost = next(
        node
        for node in value["nodes"]
        if node["entity_id"] == complete_library[3]["cases"]["history_missing"]
    )
    assert ghost["reason"] == "identity_not_indexed" and ghost["historical"]
    assert not ghost["note_id"] and ghost["sha256"] is None
    encoded = json.dumps(value, ensure_ascii=False)
    assert (
        "PRIVATE" not in encoded
        and complete_library[3]["cases"]["restricted_missing"] not in encoded
    )


def test_search_and_projection_share_token_and_all_entity_types(config, complete_library):
    value = read(config, complete_library, max_nodes=1)
    client, vault, _, fixture = complete_library
    with database.connect(config.state_root) as db:
        expert = cloud.search(config, db, client["id"], vault, "Expert 1")
        ghost = cloud.search(config, db, client["id"], vault, "已移走")
        private = cloud.search(config, db, client["id"], vault, "PRIVATE")
    assert expert["items"][0]["kind"] == "expert" and ghost["items"][0]["kind"] == "unresolved"
    assert value["snapshot_token"] == expert["snapshot_token"] == ghost["snapshot_token"]
    assert not private["items"]
    focused = read(config, complete_library, max_nodes=1, focus=expert["items"][0]["id"])
    assert focused["nodes"][0]["id"] == fixture["experts"][0]["id"]


def test_current_replacement_at_historical_path_never_guesses_old_identity(
    config, complete_library
):
    client, vault, root, fixture = complete_library
    old = read(config, complete_library)
    ghost = next(
        node for node in old["nodes"] if node["entity_id"] == fixture["cases"]["history_missing"]
    )
    with database.connect(config.state_root) as db:
        source = cloud_sources.read(config, db, client["id"], vault)
    (root / ghost["path"]).write_text(
        "# A different current note\nA replacement, not the saved identity.\n"
    )
    with pytest.raises(IntegrityError, match="layout_tracked_note_replaced"):
        note_search_build.build(config, client["id"], vault)
    replacement = replace(
        note_index.read_note(root, ghost["path"], note_index.Limits().note_bytes), text=""
    )
    source = replace(source, notes={**source.notes, ghost["path"]: replacement})
    with database.connect(config.state_root) as db:
        table = cloud_entities.build(config, db, client["id"], vault, source)
    current = next(
        node
        for node in table.nodes.values()
        if node["path"] == ghost["path"] and node["availability"] == "current"
    )
    historical = table.nodes[ghost["id"]]
    assert current["id"] != historical["id"] and historical["reason"] == "identity_not_indexed"
    assert table.aliases[fixture["cases"]["history_missing"]] != current["id"]


def test_registry_version_title_and_decision_changes_invalidate_shared_token(
    config, complete_library
):
    client, vault, _, fixture = complete_library
    before = read(config, complete_library)
    external = fixture["experts"][0]
    package = store.get(config, "packages", external["registry_id"])
    title = "A complete expert title " * 20
    store.put(
        config,
        "packages",
        external["registry_id"],
        vault,
        {**package, "title": title, "review_state": "reviewed", "artifact_stage": "reference"},
    )
    title_changed = read(config, complete_library)
    node = next(node for node in title_changed["nodes"] if node["id"] == external["id"])
    assert (
        node["label"] == title
        and node["review_state"] == "reviewed"
        and node["artifact_stage"] == "reference"
    )
    assert before["snapshot_token"] != title_changed["snapshot_token"]
    store.put(config, "packages", external["registry_id"], vault, {**package, "version": "a" * 64})
    version_changed = read(config, complete_library)
    assert external["id"] not in {node["id"] for node in version_changed["nodes"]}
    assert version_changed["coverage"]["excluded_expert_entries"] == 1
    assert not version_changed["coverage"]["complete"]
    assert title_changed["snapshot_token"] != version_changed["snapshot_token"]
    entity = fixture["cases"]["manual_unpinned"]
    mid = fixture["mocs"]["ownership"]["id"]
    store.put(
        config,
        "decisions",
        vault + ":" + entity + ":" + mid,
        vault,
        {"entity": entity, "moc": mid, "state": "pinned"},
    )
    pinned = read(config, complete_library)
    assert pinned["snapshot_token"] != version_changed["snapshot_token"]
    assert pinned["coverage"]["manual_locked_memberships"] == 2


@pytest.mark.parametrize("state", ["ready", "absent", "stale", "unavailable"])
def test_quality_only_hides_experts_orphans_and_ledger_before_every_index_branch(
    config, complete_library, state
):
    client, vault, root, _ = complete_library
    if state == "absent":
        note_search_cache.location(config, vault).unlink()
    elif state == "stale":
        (root / "new.md").write_text("# Change\n")
    elif state == "unavailable":
        import sqlite3

        with sqlite3.connect(note_search_cache.location(config, vault)) as cached:
            cached.execute("DELETE FROM generation")
    with database.connect(config.state_root) as db:
        connection = connect(config, db, vault, "complete")
        with (
            patch.object(
                cloud_ledger, "read", side_effect=AssertionError("restricted ledger read")
            ),
            patch.object(
                note_search_cache, "connection", side_effect=AssertionError("restricted index read")
            ),
        ):
            value = cloud.read(config, db, connection["client_id"], vault)
            found = cloud.search(config, db, connection["client_id"], vault, "Expert")
    assert not value["nodes"] and not value["edges"] and not found["items"]
    assert value["coverage"]["current_experts"] == value["coverage"]["unresolved_targets"] == 0
    assert value["coverage"]["moc_membership_unavailable"] == 0


def test_published_experts_outside_current_scope_never_reappear_via_index_metadata(
    config, complete_library
):
    client, vault, root, fixture = complete_library
    with database.connect(config.state_root) as db:
        source = cloud_sources.read(config, db, client["id"], vault)
    with note_search_cache.connection(config, vault) as cached:
        indexed = {
            row[0]: (row[1], row[2]) for row in cached.execute("SELECT * FROM indexed_experts")
        }
    native = fixture["experts"][1]
    excluded = sorted({"Restricted", "Book", native["path"].split("/")[0]})
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            (json.dumps({"include": [""], "exclude": excluded}), vault),
        )
    with patch.object(note_index, "read_note", side_effect=AssertionError("source body")):
        with database.connect(config.state_root) as db:
            admitted, _, _ = cloud_experts.read(
                config, db, client["id"], vault, root, source.notes, indexed
            )
        assert not admitted and indexed[native["registry_id"]][1] == native["path"]
        value = read(config, complete_library)
    assert not any(node["kind"] == "expert" for node in value["nodes"])
    encoded = json.dumps(value, ensure_ascii=False)
    assert native["path"] not in encoded and "Markdown 专家" not in encoded


def test_saved_analysis_history_never_bypasses_audience_with_orphan_metadata(
    config, complete_library
):
    client, vault, _, fixture = complete_library
    old = read(config, complete_library)
    ghost = next(
        node for node in old["nodes"] if node["entity_id"] == fixture["cases"]["history_missing"]
    )
    with (
        patch(
            "fiber_bundle.vault_merge_analysis.records", return_value=[{"note_path": ghost["path"]}]
        ),
        patch("fiber_bundle.document_creation_save.search_source", side_effect=auth.Denied(404)),
    ):
        value = read(config, complete_library)
        with database.connect(config.state_root) as db:
            found = cloud.search(config, db, client["id"], vault, "已移走")
    hidden = next(node for node in value["nodes"] if node["id"] == ghost["id"])
    assert not hidden["metadata_visible"] and hidden["path"] == "" and hidden["entity_id"] is None
    assert not found["items"] and ghost["label"] not in json.dumps(value, ensure_ascii=False)


@pytest.mark.parametrize("competing", [False, True])
def test_exact_physical_match_never_overrides_an_explicit_other_identity(
    config, complete_library, competing
):
    client, vault, _, fixture = complete_library
    with database.connect(config.state_root) as db:
        source = cloud_sources.read(config, db, client["id"], vault)
    ledger = cloud_ledger.read(config, vault)
    actual_id = fixture["cases"]["manual_pinned"]
    actual = ledger.registrations[actual_id]
    other_id = "note:" + str(uuid4())
    other = replace(actual, identity=other_id)
    registrations = {
        key: value for key, value in ledger.registrations.items() if key != actual_id or competing
    }
    registrations[other_id] = other
    mid = fixture["mocs"]["ownership"]["id"]
    records = dict(ledger.records)
    records[mid] = replace(records[mid], members={**records[mid].members, other_id: ["system"]})
    injected = replace(ledger, registrations=registrations, records=records)
    with (
        patch.object(cloud_ledger, "read", return_value=injected),
        database.connect(config.state_root) as db,
    ):
        table = cloud_entities.build(config, db, client["id"], vault, source)
    assert table.aliases[other_id] != table.aliases[actual_id]
    assert table.nodes[table.aliases[other_id]]["kind"] == "unresolved"


def test_expert_path_alias_cached_chunks_cannot_supply_unadmitted_source_edges(
    config, complete_library
):
    client, vault, _, fixture = complete_library
    import sqlite3

    alias = fixture["experts"][0]["path"] + "/SKILL.md"
    with sqlite3.connect(note_search_cache.location(config, vault)) as cached:
        cached.execute(
            "INSERT INTO chunks VALUES(?,?,?,?,?)", (alias, 1, "[[方法/不确定度]]", "", "")
        )
    value = read(config, complete_library)
    expert_id = fixture["experts"][0]["id"]
    assert not any(
        edge["kind"] == "explicit" and expert_id in (edge["source"], edge["target"])
        for edge in value["edges"]
    )


def test_legacy_unregistered_type_moc_has_same_kind_and_token_in_search(config, complete_library):
    client, vault, root, _ = complete_library
    path = root / "Map/Legacy map.md"
    path.write_text("---\ntype: moc\n---\n# Legacy map\n[[方法/不确定度]]\n")
    note_search_build.build(config, client["id"], vault)
    value = read(config, complete_library)
    with database.connect(config.state_root) as db:
        found = cloud.search(config, db, client["id"], vault, "Legacy map")
    node = next(node for node in value["nodes"] if node["path"] == "Map/Legacy map.md")
    assert node["kind"] == found["items"][0]["kind"] == "moc"
    assert value["snapshot_token"] == found["snapshot_token"]
