"""Graph selectors admit current partial indexes without weakening selected-source checks."""

from unittest.mock import patch

import pytest
from test_vault_sync_reconcile import observe

from fiber_bundle import auth, database, note_index, note_inventory, note_search_cache
from fiber_bundle.config import IntegrityError
from fiber_bundle.knowledge_graph import intake, note_listing, projection
from fiber_bundle.vault_sync import index, scanner, store

from .test_lifecycle import small as small


@pytest.fixture
def partial(small):
    config, cid, vid, *_ = small
    root = config.project_root / "test vault/temp_vault1"
    observe(config, ({"id": cid}, vid, root))
    scanner.heartbeat(config, vid)
    reader = note_index.read_note

    def available(root, path, limit, **kwargs):
        if path == "n2.md":
            raise IntegrityError("cloud_item_not_downloaded")
        return reader(root, path, limit, **kwargs)

    with patch.object(note_index, "read_note", available):
        result = index.update(config, cid, vid)
    assert result["needs_attention"] == 1
    return small


def test_partial_note_list_uses_current_index_and_explains_coverage(partial):
    config, cid, vid, selected, token, *_ = partial
    with database.connect(config.state_root) as db:
        with patch.object(note_inventory, "read", side_effect=AssertionError("recursive scan")):
            result = note_listing.page(config, db, cid, vid)
        assert {r["note_path"] for r in result["items"]} == {"n0.md", "n1.md"}
        assert all(r["selectable"] for r in result["items"])
        assert result["coverage"] == {
            "complete": False,
            "unavailable_count": 1,
            "unavailable": [{"path": "n2.md", "reason": "vault_sync_cloud_pending"}],
        }
        assert note_listing.listing(config, db, cid, vid) == result["items"]
        graph = projection.read(config, db, cid, vid)
        assert graph["statistics"]["registered_notes"] == 2
        assert not graph["coverage"]["complete"]
    prepared = intake.prepare(config, token, vid, selected[:1], enabled=True)
    assert len(prepared.request.notes) == 1
    assert "2.4 MPa" in prepared.request.notes[0].text


def test_unobserved_edit_blocks_selected_body_and_never_uses_old_index_text(partial):
    config, _, vid, selected, token, *_ = partial
    path = config.project_root / "test vault/temp_vault1/n0.md"
    path.write_text("# 已人工改正\n当前正文已修改。\n")
    with pytest.raises(IntegrityError, match="source changed"):
        intake.prepare(config, token, vid, selected[:1], enabled=True)
    assert path.read_text() == "# 已人工改正\n当前正文已修改。\n"


@pytest.mark.parametrize("problem", ["observer", "pending", "visibility"])
def test_partial_status_alone_does_not_authorize_a_stale_index(partial, problem):
    config, cid, vid, *_ = partial
    with store.connection(config, write=True) as side:
        if problem == "observer":
            side.execute("UPDATE libraries SET heartbeat=0 WHERE id=?", (vid,))
        elif problem == "pending":
            side.execute("UPDATE libraries SET generation=generation+1 WHERE id=?", (vid,))
    with database.connect(config.state_root) as db:
        if problem == "visibility":
            with note_search_cache.connection(config, vid) as cached:
                # Read-only index; simulate changed evidence at the proof boundary instead.
                from fiber_bundle.vault_sync.visibility import Proof

                assert cached is not None
            with patch(
                "fiber_bundle.vault_sync.visibility.read", return_value=Proof("changed", {})
            ):
                with pytest.raises(note_search_cache.IndexUnavailable):
                    note_listing.page(config, db, cid, vid)
        else:
            with pytest.raises(note_search_cache.IndexUnavailable):
                note_listing.page(config, db, cid, vid)


def test_revocation_is_checked_before_any_partial_metadata_is_returned(partial):
    config, cid, vid, *_ = partial
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=False, original=False, add=False)
        with pytest.raises(auth.Denied):
            note_listing.page(config, db, cid, vid)


def test_http_notes_preserve_array_contract_and_offer_explicit_coverage(partial):
    from test_execution_leases import client

    config, cid, vid, *_ = partial
    c = client(config)
    scope = {"client_id": cid, "vault_id": vid}
    old = c.get("/admin/knowledge-graph/notes", params=scope)
    current = c.get("/admin/knowledge-graph/notes", params={**scope, "include_coverage": True})
    assert old.status_code == current.status_code == 200
    assert isinstance(old.json(), list)
    assert current.json()["items"] == old.json()
    assert current.json()["coverage"]["unavailable_count"] == 1
    c.close()
