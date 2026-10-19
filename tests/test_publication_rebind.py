"""External physical changes are repaired explicitly without merging duplicate notes."""

import shutil
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_placement import published

from fiber_bundle import (
    auth,
    database,
    moc_store,
    note_inventory,
    note_placement,
    note_provenance,
    publication_locations,
    publication_rebind,
)
from fiber_bundle.config import IntegrityError


@pytest.mark.parametrize("change", ["move", "atomic_edit"])
def test_explicit_rebind_preserves_source_identity_and_manual_moc(
    config, policy, article_scope, change
):
    _, result, note = published(config, policy, article_scope)
    root = note.parent.parent
    identity = "publication:" + result["publication_id"]
    mid = str(uuid4())
    key = root.stat()
    moc_store.save(
        config,
        article_scope[1],
        mid,
        article_scope[0],
        moc_store.Record("MOC", key.st_dev, key.st_ino, 1, {identity: ["manual"]}),
        "create",
        0,
    )
    target = root / "Archive/External.md" if change == "move" else note
    if change == "move":
        note.rename(target)
    else:
        temporary = note.with_suffix(".tmp")
        temporary.write_text(note.read_text() + "\nmanual external editor revision\n")
        temporary.replace(note)
    before = target.read_bytes()
    with database.connect(config.state_root) as db:
        grant = auth.grant(db, *article_scope[:2], "query")
        with pytest.raises(IntegrityError, match="layout_rebind_required"):
            note_inventory.read(config, db, grant)
        assert note_placement.listing(config, db, *article_scope[:2])["rebind_required"]
        preview = publication_rebind.preview(
            config,
            db,
            *article_scope[:2],
            "article",
            result["publication_id"],
            1,
            target.relative_to(root).as_posix(),
        )
        assert preview["vault_write_performed"] is False and target.read_bytes() == before
        applied = publication_rebind.apply(config, db, *article_scope[:2], preview["operation_id"])
        assert applied["state"] == "completed" and target.read_bytes() == before
        inventory = note_inventory.read(config, db, grant)
        current = next(n for n in inventory.notes if n.identity == identity)
        assert current.relative_path == target.relative_to(root).as_posix()
        provenance = note_provenance.describe(
            config, db, article_scope[0], grant, inventory, current
        )
        assert provenance["source_kind"] == (
            "user_revision" if change == "atomic_edit" else "published_article"
        )
        assert provenance["originals"]
        assert moc_store.load(config, article_scope[1], mid).members == {identity: ["manual"]}
        assert publication_locations.get(db, "article", result["publication_id"])["revision"] == 2
        assert (
            publication_rebind.apply(config, db, *article_scope[:2], preview["operation_id"])[
                "state"
            ]
            == "completed"
        )


def test_rebind_rejects_live_original_copy_and_changed_preview(config, policy, article_scope):
    _, result, note = published(config, policy, article_scope)
    root = note.parent.parent
    copy = root / "Archive/Copy.md"
    shutil.copyfile(note, copy)
    with database.connect(config.state_root) as db:
        with pytest.raises(IntegrityError, match="original_still_present"):
            publication_rebind.preview(
                config,
                db,
                *article_scope[:2],
                "article",
                result["publication_id"],
                1,
                "Archive/Copy.md",
            )
        copy.unlink()
        note.rename(copy)
        preview = publication_rebind.preview(
            config,
            db,
            *article_scope[:2],
            "article",
            result["publication_id"],
            1,
            "Archive/Copy.md",
        )
        copy.write_text(copy.read_text() + "new edit")
        with pytest.raises(IntegrityError, match="preview_changed"):
            publication_rebind.apply(config, db, *article_scope[:2], preview["operation_id"])
        assert copy.read_text().endswith("new edit")


def test_rebind_http_requires_admin_csrf_and_explicit_confirmation(
    config, policy, article_scope, http
):
    from fastapi.testclient import TestClient

    from fiber_bundle.api import create_app

    _, result, note = published(config, policy, article_scope)
    target = note.parent / "External.md"
    note.rename(target)
    body = {
        "client_id": article_scope[0],
        "vault_id": article_scope[1],
        "kind": "article",
        "publication_id": result["publication_id"],
        "expected_revision": 1,
        "entry_note_path": "Inbox/External.md",
    }
    prefix = "/admin/vault-publication-rebinds"
    with TestClient(
        create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1)
    ) as anonymous:
        assert anonymous.post(prefix + "/preview", json=body).status_code in (401, 403)
    assert (
        http.post(prefix + "/preview", json=body, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    response = http.post(prefix + "/preview", json=body)
    assert response.status_code == 200, response.text
    operation = response.json()["operation_id"]
    scope = {key: body[key] for key in ("client_id", "vault_id")}
    assert (
        http.post(f"{prefix}/{operation}/apply", json={**scope, "confirm": False}).status_code
        == 409
    )
    assert (
        http.post(f"{prefix}/{operation}/apply", json={**scope, "confirm": True}).json()["state"]
        == "completed"
    )
