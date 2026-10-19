"""Ordinary tracked notes and Maps retain logical IDs after external atomic saves."""

import shutil
import time
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_identities import track
from test_publication_reconcile import choice

from fiber_bundle import (
    auth,
    database,
    moc_store,
    note_identity_rebind,
    note_identity_store,
    note_index,
    note_inventory,
    note_search_build,
    publication_reconcile,
    publication_reconcile_scan,
)
from fiber_bundle.config import IntegrityError


def atomic(path, text=None):
    temp = path.with_suffix(".replacement")
    temp.write_bytes(path.read_bytes() if text is None else text.encode())
    temp.replace(path)


@pytest.mark.parametrize("change", ["same_bytes", "edited", "map", "move", "copy_move"])
def test_ordinary_rebind_keeps_stable_id_manual_members_and_current_attributes(
    config, identity_scope, change
):
    client, vault, root = identity_scope
    registration = track(config, identity_scope)
    note = root / "manual.md"
    mid = str(uuid4())
    key = root.stat()
    moc_store.save(
        config,
        vault,
        mid,
        client["id"],
        moc_store.Record(
            "Manual MOC", key.st_dev, key.st_ino, 1, {registration["note_identity"]: ["manual"]}
        ),
        "create",
        0,
    )
    if change == "map":
        note.rename(root / "Map.md")
        # Keep the original registered path for a Map-style same-path replacement.
        old = note_identity_store.records(config, vault)[0]
        note_identity_store.save(
            config,
            vault,
            client["id"],
            replace(old, relative_path="Map.md", revision=2),
            expected_revision=1,
        )
        note = root / "Map.md"
    if change in {"same_bytes", "edited", "map"}:
        atomic(
            note,
            None
            if change == "same_bytes"
            else "---\ntags: [human]\nmoc: ['[[Manual MOC]]']\n---\n# User edits\nkept\n",
        )
    elif change == "move":
        note.rename(root / "Moved.md")
        note = root / "Moved.md"
    else:
        shutil.copy2(note, root / "Moved.md")
        note.unlink()
        note = root / "Moved.md"
    raw = note.read_bytes()
    with database.connect(config.state_root) as db:
        rows = publication_reconcile_scan.scan(config, db, client["id"], vault)["items"]
        assert len(rows) == 1 and rows[0]["kind"] == "note"
        assert rows[0]["suggested_path"] == note.name
        packet = publication_reconcile.preview(config, db, client["id"], vault, [choice(rows[0])])
        assert note.read_bytes() == raw
        done = publication_reconcile.apply(config, db, client["id"], vault, packet["operation_id"])
        assert done["state"] == "completed", done
        notes = note_inventory.read(config, db, auth.grant(db, client["id"], vault, "query")).notes
        assert (
            next(n for n in notes if n.relative_path == note.name).identity
            == registration["note_identity"]
        )
        assert moc_store.load(config, vault, mid).members == {
            registration["note_identity"]: ["manual"]
        }
        count = len(note_identity_store.records(config, vault))
        assert (
            publication_reconcile.apply(config, db, client["id"], vault, packet["operation_id"])[
                "state"
            ]
            == "completed"
        )
        assert len(note_identity_store.records(config, vault)) == count
    assert note.read_bytes() == raw
    assert note_search_build.build(config, client["id"], vault)["state"] == "ready"


def test_index_http_has_repair_code_and_real_batch_restores_index(config, identity_scope, http):
    client, vault, root = identity_scope
    track(config, identity_scope)
    atomic(root / "manual.md")
    scope = {"client_id": client["id"], "vault_id": vault}
    response = http.post("/admin/note-index/update", json=scope)
    assert (
        response.status_code == 409 and response.json()["error"] == "layout_tracked_note_replaced"
    )
    base = "/admin/vault-publication-rebinds"
    item = http.post(base + "/scan", json=scope).json()["items"][0]
    packet = http.post(base + "/batch/preview", json={**scope, "items": [choice(item)]}).json()
    assert (
        http.post(
            base + "/batch/" + packet["operation_id"] + "/apply", json={**scope, "confirm": True}
        ).json()["state"]
        == "completed"
    )
    response = http.post("/admin/note-index/update", json=scope)
    assert response.status_code == 200 and response.json()["state"] == "ready"


@pytest.mark.parametrize("change", ["new_file", "in_place", "atomic"])
def test_directory_or_content_change_rejects_remaining_frozen_work(config, identity_scope, change):
    client, vault, root = identity_scope
    track(config, identity_scope)
    atomic(root / "manual.md")
    with database.connect(config.state_root) as db:
        item = publication_reconcile_scan.scan(config, db, client["id"], vault)["items"][0]
        packet = publication_reconcile.preview(config, db, client["id"], vault, [choice(item)])
        if change == "new_file":
            (root / "new.md").write_text("new external note")
        elif change == "in_place":
            (root / "manual.md").write_text("changed after preview")
        else:
            atomic(root / "manual.md", "replaced after preview")
        done = publication_reconcile.apply(config, db, client["id"], vault, packet["operation_id"])
        assert (
            done["state"] == "partial"
            and done["items"][0]["error"] == "layout_rebind_preview_changed"
        )
        assert note_identity_store.records(config, vault)[0].revision == 1


def test_interrupted_sidecar_commit_is_acknowledged_without_new_revision(config, identity_scope):
    client, vault, root = identity_scope
    track(config, identity_scope)
    atomic(root / "manual.md")
    original = note_identity_store.save

    def interrupted(*a, **kw):
        original(*a, **kw)
        raise SystemExit("after durable sidecar commit")

    with database.connect(config.state_root) as db:
        item = publication_reconcile_scan.scan(config, db, client["id"], vault)["items"][0]
        packet = publication_reconcile.preview(config, db, client["id"], vault, [choice(item)])
        with (
            patch.object(note_identity_store, "save", side_effect=interrupted),
            pytest.raises(SystemExit),
        ):
            publication_reconcile.apply(config, db, client["id"], vault, packet["operation_id"])
        assert (
            publication_reconcile.apply(config, db, client["id"], vault, packet["operation_id"])[
                "state"
            ]
            == "completed"
        )
        assert note_identity_store.records(config, vault)[0].revision == 2


def test_294_replacements_share_snapshot_and_keep_all_ids(config, identity_scope):
    client, vault, root = identity_scope
    # Seed real registrations directly; registration itself is covered by note identity tests.
    for i in range(294):
        path = root / f"note-{i}.md"
        path.write_text(f"# Synthetic {i}\nbody {i}")
        note = note_index.read_note(root, path.name, 1024)
        note_identity_store.save(
            config,
            vault,
            client["id"],
            note_identity_store.Registration(
                "note:" + str(uuid4()),
                None,
                path.name,
                note.sha256,
                note.file_key,
                (root.stat().st_dev, root.stat().st_ino),
                1,
            ),
        )
    old = {r.identity: r.relative_path for r in note_identity_store.records(config, vault)}
    for path in root.glob("note-*.md"):
        atomic(path)
    started = time.monotonic()
    with (
        database.connect(config.state_root) as db,
        patch.object(
            note_identity_rebind, "context", wraps=note_identity_rebind.context
        ) as snapshot,
    ):
        items = publication_reconcile_scan.scan(config, db, client["id"], vault)["items"]
        assert len(items) == 294
        packet = publication_reconcile.preview(
            config, db, client["id"], vault, [choice(i) for i in items]
        )
        result = publication_reconcile.apply(
            config, db, client["id"], vault, packet["operation_id"]
        )
        assert result["state"] == "completed", result
        assert snapshot.call_count == 4  # scan, preview scan, shared preview, shared apply
    assert {r.identity: r.relative_path for r in note_identity_store.records(config, vault)} == old
    assert note_search_build.build(config, client["id"], vault)["state"] == "ready"
    print(f"294 replacements in {time.monotonic() - started:.3f}s")


def test_explicit_small_chunks_make_progress_and_continue_without_duplicate_ids(
    config, identity_scope
):
    client, vault, root = identity_scope
    track(config, identity_scope)
    other = root / "second.md"
    other.write_text("# second\ntext")
    track(config, identity_scope, "second.md")
    atomic(root / "manual.md")
    atomic(other)
    with database.connect(config.state_root) as db:
        items = publication_reconcile_scan.scan(config, db, client["id"], vault)["items"]
        packet = publication_reconcile.preview(
            config, db, client["id"], vault, [choice(i) for i in items]
        )
        first = publication_reconcile.apply(
            config, db, client["id"], vault, packet["operation_id"], max_items=1
        )
        assert (
            first["state"] == "running"
            and sum(i["state"] == "completed" for i in first["items"]) == 1
        )
        final = publication_reconcile.apply(
            config, db, client["id"], vault, packet["operation_id"], max_items=1
        )
        assert final["state"] == "completed"
        assert all(r.revision == 2 for r in note_identity_store.records(config, vault))


@pytest.mark.parametrize("conflict", ["existing_identity", "visible_original"])
def test_cannot_steal_another_identity_or_merge_visible_original(config, identity_scope, conflict):
    client, vault, root = identity_scope
    track(config, identity_scope)
    other = root / "second.md"
    other.write_text("# second")
    track(config, identity_scope, "second.md")
    source = root / "manual.md"
    if conflict == "existing_identity":
        atomic(source)
        target = "second.md"
        error = "identity_collision"
    else:
        source.rename(root / "original.md")
        shutil.copy2(root / "original.md", source)
        target = "manual.md"
        error = "original_still_present"
    with database.connect(config.state_root) as db:
        item = next(
            i
            for i in publication_reconcile_scan.scan(config, db, client["id"], vault)["items"]
            if i["source_path"] == "manual.md"
        )
        with pytest.raises(IntegrityError, match=error):
            publication_reconcile.preview(
                config, db, client["id"], vault, [choice(item, target=target)]
            )
