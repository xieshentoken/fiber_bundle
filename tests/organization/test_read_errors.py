"""Authenticated preview failures name bounded causes and retain every source byte."""

from unittest.mock import patch

from test_execution_leases import client
from test_jobs import ready as ready

from fiber_bundle import note_index
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import store


def test_moc_cloud_failure_is_typed_without_exposing_absolute_storage_paths(config, ready):
    cid, vid, root, moc, note = ready
    before = {p: p.read_bytes() for p in (moc, note)}
    original = note_index.read_note

    def reader(root, path, *args, **kwargs):
        if path.startswith("Map/"):
            raise IntegrityError("cloud_item_not_downloaded")
        return original(root, path, *args, **kwargs)

    c = client(config)
    with patch.object(note_index, "read_note", reader):
        result = c.post(
            "/admin/organization/maintenance",
            json={"client_id": cid, "vault_id": vid, "mode": "quick", "new_mocs": []},
        )
    assert result.status_code == 409, result.text
    assert result.json() == {
        "error": "organization_moc_unavailable",
        "issues": [
            {"path": moc.relative_to(root).as_posix(), "reason": "vault_sync_cloud_pending"}
        ],
    }
    assert str(root) not in result.text
    assert not store.rows(config, "jobs", vid)
    assert all(p.read_bytes() == content for p, content in before.items())
    with patch.object(note_index, "snapshot", side_effect=AssertionError("catalog reread")):
        progress = c.get("/admin/organization/progress", params={"client_id": cid, "vault_id": vid})
    assert progress.status_code == 200
    c.close()
