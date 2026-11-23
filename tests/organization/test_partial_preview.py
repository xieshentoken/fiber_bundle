"""Unavailable ordinary sources are explicit omissions, never empty or restored content."""

from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview
from test_vault_sync_reconcile import observe

from fiber_bundle import database, note_index
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import catalog_inventory, maintenance, overview, packages
from fiber_bundle.vault_sync import scanner, store


@pytest.mark.parametrize("mode", ["quick", "additive", "deep"])
def test_unreadable_ordinary_note_does_not_block_preview_or_overwrite_edits(config, ready, mode):
    cid, vid, root, moc, note = ready
    edited = root / "Inbox/edited.md"
    edited.write_text("# 人工改正的内容\n#热管理\n当前版本，不是原提取结果。\n")
    before = {p: p.read_bytes() for p in (edited, moc, note)}
    reader = note_index.read_note

    def local(root, path, limit, **kwargs):
        if path == "Inbox/edited.md":
            raise IntegrityError("cloud_item_not_downloaded")
        return reader(root, path, limit, **kwargs)

    with database.connect(config.state_root) as db, patch.object(note_index, "read_note", local):
        job = preview(config, db, cid, vid, mode)
        assert [i["path"] for i in job["items"]] == ["Inbox/传热.md"]
        public = maintenance.public(job)
        assert public["coverage"] == {
            "complete": False,
            "unavailable_count": 1,
            "unavailable": [{"path": "Inbox/edited.md", "reason": "vault_sync_cloud_pending"}],
        }
        assert not job["confirmed"] and not job["model_called"]
        assert overview.browse(config, db, cid, vid)["coverage"] == public["coverage"]
        if mode == "quick":
            assert job["items"][0]["target"] is None
            assert job["items"][0]["deferred_target"] == "Resource/传热.md"
            assert public["application_blocked"] is None
    assert all(p.read_bytes() == content for p, content in before.items())
    with database.connect(config.state_root) as db:
        # Recovery admits the current human-edited version; it has no special exemption.
        recovered = preview(config, db, cid, vid, mode)
        item = next(i for i in recovered["items"] if i["path"] == "Inbox/edited.md")
        assert item["title"] == "人工改正的内容"
        assert "coverage" not in recovered["fixed"]
        with pytest.raises(IntegrityError):
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])


def test_unavailable_moc_cannot_be_treated_as_no_memberships(config, ready):
    cid, vid, root, moc, _ = ready
    before = moc.read_bytes()
    reader = note_index.read_note

    def local(root, path, limit, **kwargs):
        if path.startswith("Map/"):
            raise IntegrityError("cloud_item_not_downloaded")
        return reader(root, path, limit, **kwargs)

    with database.connect(config.state_root) as db, patch.object(note_index, "read_note", local):
        for mode in ("quick", "additive", "deep"):
            with pytest.raises(catalog_inventory.Unavailable, match="organization_moc_unavailable"):
                maintenance.submit(config, db, cid, vid, mode)
    assert moc.read_bytes() == before


def test_managed_preview_reuses_catalog_and_does_not_retry_waiting_body(config, ready):
    cid, vid, root, _, _ = ready
    bad = root / "Inbox/waiting.md"
    bad.write_text("# Not locally readable\n")
    observe(config, ({"id": cid}, vid, root))
    scanner.heartbeat(config, vid)
    with store.connection(config, write=True) as side:
        side.execute(
            "INSERT INTO issues VALUES(?,?,?,0,9999999999,1)",
            (vid, "Inbox/waiting.md", "vault_sync_cloud_pending"),
        )
    reader = note_index.read_note

    def guarded(root, path, limit, **kwargs):
        assert path != "Inbox/waiting.md"
        return reader(root, path, limit, **kwargs)

    with database.connect(config.state_root) as db, patch.object(note_index, "read_note", guarded):
        job = preview(config, db, cid, vid, "quick")
        assert job["fixed"]["coverage"]["unavailable_count"] == 1
        assert len(job["items"]) == 1
        # A not-yet-observed new MOC invalidates the catalog before confirmation.
        (root / "Map/new MOC.md").write_text("# new MOC\n")
        with pytest.raises(catalog_inventory.Unavailable, match="catalog_changed"):
            maintenance.validate(config, db, job)


def test_managed_observer_failure_does_not_fall_back_to_recursive_scan(config, ready):
    cid, vid, root, *_ = ready
    observe(config, ({"id": cid}, vid, root))
    with store.connection(config, write=True) as side:
        side.execute("UPDATE libraries SET heartbeat=0 WHERE id=?", (vid,))
    with database.connect(config.state_root) as db:
        with pytest.raises(catalog_inventory.Unavailable, match="catalog_pending"):
            maintenance.submit(config, db, cid, vid, "quick")


def test_draft_and_ordinary_note_remain_in_preview_with_one_unavailable_note(
    config, ready, package
):
    cid, vid, root, moc, note = ready
    destination = root / "Inbox/fake-thermal"
    package.rename(destination)
    before = {p: p.read_bytes() for p in destination.rglob("*") if p.is_file()}
    before.update({p: p.read_bytes() for p in (moc, note)})
    (root / "Inbox/waiting.md").write_text("# Edited and awaiting cloud\n")
    reader = note_index.read_note

    def local(root, path, limit, **kwargs):
        if path == "Inbox/waiting.md":
            raise IntegrityError("cloud_item_not_downloaded")
        return reader(root, path, limit, **kwargs)

    with database.connect(config.state_root) as db:
        packages.register(config, db, cid, vid, "Inbox/fake-thermal")
        with patch.object(note_index, "read_note", local):
            job = maintenance.submit(config, db, cid, vid, "quick")
            assert {i["kind"] for i in job["items"]} == {"note", "expert"}
            assert job["fixed"]["coverage"]["unavailable_count"] == 1
            assert not job["confirmed"] and not job["model_called"]
    assert all(p.read_bytes() == raw for p, raw in before.items())
