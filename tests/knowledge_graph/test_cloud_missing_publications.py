"""Availability evidence permits history only; privacy and freshness still apply."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from note_cloud_complete_fixture import confirmed_missing_intake, save_members

from fiber_bundle import auth, database, moc_store, note_search_build, note_search_cache
from fiber_bundle.inputs import canonical
from fiber_bundle.knowledge_graph import cloud_ledger, cloud_links, cloud_missing_publications

from .test_cloud_complete import complete_library as complete_library
from .test_cloud_complete import read
from .test_cloud_web_contract import validate_frontend


@pytest.fixture
def confirmed(config, complete_library):
    client, vault, root, fixture = complete_library
    value = confirmed_missing_intake(config, client, vault, root)
    mid = fixture["mocs"]["ownership"]["id"]
    record = moc_store.load(config, vault, mid)
    save_members(config, client, vault, mid, {**record.members, value["identity"]: ["manual"]})
    note_search_build.build(config, client["id"], vault)
    return value


def proof(config, complete_library, confirmed):
    client, vault, root, _ = complete_library
    ledger = cloud_ledger.read(config, vault)
    with database.connect(config.state_root) as db:
        return cloud_missing_publications.read(
            config, db, client["id"], vault, root, ledger.registrations, {confirmed["identity"]}
        )


def edit_operation(config, confirmed, key, value):
    with database.connect(config.state_root) as db:
        if key == "state":
            db.execute(
                "UPDATE vault_operations SET state=? WHERE id=?",
                (value, confirmed["operation_id"]),
            )
        else:
            payload = json.loads(
                db.execute(
                    "SELECT payload FROM vault_operations WHERE id=?", (confirmed["operation_id"],)
                ).fetchone()[0]
            )
            payload[key] = value
            db.execute(
                "UPDATE vault_operations SET payload=? WHERE id=?",
                (canonical(payload), confirmed["operation_id"]),
            )


@pytest.mark.parametrize(
    "key,value",
    [
        ("state", "prepared"),
        ("kind", "article"),
        ("revision", 0),
        ("root_key", [0, 0]),
        ("entry_note_path", "Other.md"),
        ("missing_paths", ["Other.md"]),
        ("location_sha256", "invalid"),
    ],
)
def test_incomplete_or_conflicting_missing_operation_keeps_history_anonymous(
    config, complete_library, confirmed, key, value
):
    before = proof(config, complete_library, confirmed)
    assert before.visible == {confirmed["identity"]: confirmed["path"]}
    original = read(config, complete_library)
    target = next(
        node["id"] for node in original["nodes"] if node["entity_id"] == confirmed["identity"]
    )
    edit_operation(config, confirmed, key, value)
    after = proof(config, complete_library, confirmed)
    assert not after.visible and before.token != after.token
    graph = read(config, complete_library)
    missing = next(node for node in graph["nodes"] if node["id"] == target)
    assert not missing["metadata_visible"] and not missing["path"]
    assert confirmed["label"] not in json.dumps(graph, ensure_ascii=False)


@pytest.mark.parametrize(
    "field",
    [
        "revision",
        "published_hashes",
        "file_keys",
        "identity_overrides",
        "retained_source_root",
        "mapping-number",
        "mapping-value",
        "body-number",
        "body-string",
        "receipt",
    ],
)
def test_current_location_mismatch_cannot_grant_historical_disclosure(
    http, config, complete_library, confirmed, field
):
    before = proof(config, complete_library, confirmed)
    with database.connect(config.state_root) as db:
        if field == "revision":
            db.execute(
                "UPDATE publication_locations SET revision=revision+1 "
                "WHERE kind='intake' AND publication_id=?",
                (confirmed["publication_id"],),
            )
        else:
            # This is a tiny synthetic recovery plan produced by this fixture.
            plan = json.loads(
                db.execute(
                    "SELECT value FROM publication_locations "
                    "WHERE kind='intake' AND publication_id=?",
                    (confirmed["publication_id"],),
                ).fetchone()[0]
            )
            if field == "published_hashes":
                plan[field][confirmed["path"]] = "0" * 64
            elif field == "file_keys":
                plan[field][confirmed["path"]][0] += 1
            elif field == "identity_overrides":
                plan[field] = {"note.md": confirmed["identity"]}
            elif field == "mapping-number":
                plan["mapping"] = 10
            elif field == "mapping-value":
                plan["mapping"] = {"note.md": {"nested": "invalid"}}
            elif field == "body-number":
                plan["body_files"] = 10
            elif field == "body-string":
                plan["body_files"] = "note.md"
            elif field == "receipt":
                plan["intake_source"]["receipt_id"] = str(uuid4())
            else:
                plan[field] = "retained-source"
            db.execute(
                "UPDATE publication_locations SET value=? WHERE kind='intake' AND publication_id=?",
                (canonical(plan), confirmed["publication_id"]),
            )
    after = proof(config, complete_library, confirmed)
    assert not after.visible and before.token != after.token
    if field in {"mapping-number", "mapping-value", "body-number", "body-string"}:
        client, vault, *_ = complete_library
        scope = {"client_id": client["id"], "vault_id": vault}
        for path, parameters in [
            ("/admin/knowledge-graph/note-cloud", scope),
            ("/admin/knowledge-graph/note-cloud-search", {**scope, "query": confirmed["label"]}),
        ]:
            response = http.get(path, params=parameters)
            assert response.status_code in {409, 422, 503}
            assert set(response.json()) == {"error"}
            assert confirmed["label"] not in response.text
            assert confirmed["path"] not in response.text


def test_another_publication_claiming_the_path_prevents_historical_exception(
    config, complete_library, confirmed
):
    assert proof(config, complete_library, confirmed).visible
    with database.connect(config.state_root) as db:
        db.execute(
            "INSERT INTO publication_locations "
            "SELECT 'document',?,vault_id,entry_note_path,package_root,revision,'prepared',value "
            "FROM publication_locations WHERE kind='intake' AND publication_id=?",
            (str(uuid4()), confirmed["publication_id"]),
        )
    assert not proof(config, complete_library, confirmed).visible


def test_private_source_and_private_analysis_still_override_missing_availability(
    config, complete_library, confirmed
):
    with patch("fiber_bundle.document_creation_save.archive_visible", return_value=False):
        assert not proof(config, complete_library, confirmed).visible
        graph = read(config, complete_library)
        assert confirmed["label"] not in json.dumps(graph, ensure_ascii=False)
    with patch(
        "fiber_bundle.vault_merge_analysis.records", return_value=[{"note_path": confirmed["path"]}]
    ):
        graph = read(config, complete_library)
        assert confirmed["label"] not in json.dumps(graph, ensure_ascii=False)


def test_historical_checksum_metadata_changes_the_shared_dependency_token(
    config, complete_library, confirmed
):
    before = proof(config, complete_library, confirmed)
    from fiber_bundle.knowledge_graph import cloud

    client, vault, *_ = complete_library
    graph = read(config, complete_library)
    with database.connect(config.state_root) as db:
        found = cloud.search(config, db, client["id"], vault, confirmed["label"])
    validate_frontend(graph, found, {"client_id": client["id"], "vault_id": vault})
    edit_operation(config, confirmed, "location_sha256", "f" * 64)
    after = proof(config, complete_library, confirmed)
    # The checksum is historical metadata, not a full recovery-output rehash.
    assert after.visible == before.visible and after.token != before.token


def test_operation_drift_during_projection_is_rejected_before_delivery(
    config, complete_library, confirmed
):
    original = cloud_links.read

    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        args[1].execute(
            "UPDATE vault_operations SET state='prepared' WHERE id=?", (confirmed["operation_id"],)
        )
        return value

    with patch.object(cloud_links, "read", side_effect=changed):
        with pytest.raises(note_search_cache.IndexUnavailable, match="note_index_update_required"):
            read(config, complete_library)


def test_operation_state_and_permissions_invalidate_search_delivery(
    config, complete_library, confirmed
):
    from fiber_bundle.knowledge_graph import cloud

    client, vault, *_ = complete_library
    before = read(config, complete_library)
    edit_operation(config, confirmed, "state", "prepared")
    with database.connect(config.state_root) as db:
        found = cloud.search(config, db, client["id"], vault, confirmed["label"])
        assert not found["items"] and found["snapshot_token"] != before["snapshot_token"]
        auth.set_grant(db, client["id"], vault, query=False, original=False, add=False)
        with pytest.raises(auth.Denied):
            cloud.search(config, db, client["id"], vault, confirmed["label"])
        with pytest.raises(auth.Denied):
            cloud.read(config, db, client["id"], vault)
