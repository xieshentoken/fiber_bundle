"""One real-shape confirmed-missing intake from metadata through search/wire."""

from unittest.mock import patch

from note_cloud_complete_fixture import confirmed_missing_intake, save_members

from fiber_bundle import database, moc_store, note_index, note_search_build
from fiber_bundle.knowledge_graph import cloud

from .test_cloud_complete import complete_library as complete_library


def test_confirmed_missing_intake_history_is_visible_but_never_current(config, complete_library):
    client, vault, root, fixture = complete_library
    marker = confirmed_missing_intake(config, client, vault, root)
    mid = fixture["mocs"]["ownership"]["id"]
    record = moc_store.load(config, vault, mid)
    save_members(config, client, vault, mid, {**record.members, marker["identity"]: ["manual"]})
    note_search_build.build(config, client["id"], vault)
    with (
        patch.object(note_index, "read_note", side_effect=AssertionError("source body")),
        patch(
            "fiber_bundle.publication_availability.observe", side_effect=AssertionError("observe")
        ),
        patch("fiber_bundle.publication_availability.apply", side_effect=AssertionError("apply")),
        database.connect(config.state_root) as db,
    ):
        before = db.total_changes
        value = cloud.read(config, db, client["id"], vault)
        found = cloud.search(config, db, client["id"], vault, marker["title"])
        assert db.total_changes == before
    ghost = next(node for node in value["nodes"] if node["entity_id"] == marker["identity"])
    assert ghost["metadata_visible"] and ghost["path"] == marker["path"]
    assert ghost["reason"] == "publication_missing_confirmed"
    assert ghost["kind"] == "unresolved" and ghost["availability"] == "missing"
    assert ghost["historical"] and ghost["note_id"] is None and ghost["sha256"] is None
    assert found["items"][0]["id"] == ghost["id"]
    assert found["snapshot_token"] == value["snapshot_token"]
    assert value["coverage"]["current_notes"] == 9
    assert value["coverage"]["has_unresolved"] and not value["coverage"]["targets_resolved"]
    assert not (root / marker["path"]).exists()
