"""Batch repair composes real publication, identity, availability, and index boundaries."""

import json
import shutil
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_placement import published
from test_publication_layout import activate

from fiber_bundle import (
    auth,
    database,
    moc_store,
    note_inventory,
    note_search_build,
    publication_layout,
    publication_locations,
    publication_rebind,
    publication_reconcile,
    publication_reconcile_scan,
    vault_save_policy,
)
from fiber_bundle.config import InvalidInput


def choice(item, action="rebind", target=None):
    return {k: item[k] for k in ("kind", "publication_id", "expected_revision")} | {
        "action": action,
        "target_path": target or item["suggested_path"],
        "deletion_confirmed": action == "missing",
    }


def extra(config, db, scope, text):
    identity = str(uuid4())
    files = {"note.md": text.encode()}
    row = publication_layout.prepare(config, db, "intake", identity, scope[1], files)
    publication_layout.publish(config, db, "intake", identity, scope[0], files)
    return identity, row["entry_note_path"]


@pytest.mark.parametrize("change", ["move", "rename_modified", "atomic_edit", "hash_move"])
def test_scan_preview_rebind_retains_bytes_identity_and_manual_moc(
    config, policy, article_scope, change
):
    _, pub, note = published(config, policy, article_scope)
    root = note.parent.parent
    mid = str(uuid4())
    key = root.stat()
    identity = "publication:" + pub["publication_id"]
    moc_store.save(
        config,
        article_scope[1],
        mid,
        article_scope[0],
        moc_store.Record("人工 MOC", key.st_dev, key.st_ino, 1, {identity: ["manual"]}),
        "create",
        0,
    )
    target = note if change == "atomic_edit" else root / "Archive/Renamed.md"
    if change in {"atomic_edit", "hash_move"}:
        temp = note.with_suffix(".tmp")
        temp.write_bytes(note.read_bytes())
        if change == "atomic_edit":
            temp.write_text(temp.read_text() + "\n人工新属性\n")
        note.unlink()
        temp.rename(target)
    else:
        note.rename(target)
        if change == "rename_modified":
            target.write_text(target.read_text() + "\n人工补充\n")
    raw = target.read_bytes()
    with database.connect(config.state_root) as db:
        before = db.total_changes
        scan = publication_reconcile_scan.scan(config, db, *article_scope[:2])
        assert db.total_changes == before and len(scan["items"]) == 1
        item = scan["items"][0]
        assert item["suggested_path"] == target.relative_to(root).as_posix()
        plan = publication_reconcile.preview(config, db, *article_scope[:2], [choice(item)])
        assert target.read_bytes() == raw and plan["state"] == "prepared"
        assert publication_locations.get(db, "article", pub["publication_id"])["revision"] == 1
        applied = publication_reconcile.apply(config, db, *article_scope[:2], plan["operation_id"])
        assert applied["state"] == "completed" and not applied["vault_write_performed"]
        current = publication_locations.get(db, "article", pub["publication_id"])
        assert current["entry_note_path"] == item["suggested_path"] and current["revision"] == 2
        assert moc_store.load(config, article_scope[1], mid).members == {identity: ["manual"]}
        inventory = note_inventory.read(config, db, auth.grant(db, *article_scope[:2], "query"))
        assert (
            next(n for n in inventory.notes if n.publication_id == pub["publication_id"]).identity
            == identity
        )
        changed = db.total_changes
        assert (
            publication_reconcile.apply(config, db, *article_scope[:2], plan["operation_id"])[
                "state"
            ]
            == "completed"
        )
        assert db.total_changes == changed
    assert target.read_bytes() == raw


def test_mixed_batch_missing_and_move_recovers_index_and_preserves_history(
    config, policy, article_scope
):
    _, pub, note = published(config, policy, article_scope)
    root = note.parent.parent
    note.rename(root / "Archive/Moved.md")
    with database.connect(config.state_root) as db:
        deleted_id, deleted_path = extra(config, db, article_scope, "# 删除的笔记\n合成正文")
        (root / deleted_path).unlink()
        history = [tuple(r) for r in db.execute("SELECT * FROM article_publications")]
        scan = publication_reconcile_scan.scan(config, db, *article_scope[:2])
        assert len(scan["items"]) == 2
        choices = [
            choice(i, "missing" if i["publication_id"] == deleted_id else "rebind")
            for i in scan["items"]
        ]
        packet = publication_reconcile.preview(config, db, *article_scope[:2], choices)
        done = publication_reconcile.apply(config, db, *article_scope[:2], packet["operation_id"])
        assert done["state"] == "completed"
        assert [tuple(r) for r in db.execute("SELECT * FROM article_publications")] == history
        assert publication_locations.get(db, "intake", deleted_id)["state"] == "missing"
        assert not (root / deleted_path).exists()
        assert not publication_reconcile_scan.scan(config, db, *article_scope[:2])["items"]
    index = note_search_build.build(config, *article_scope[:2])
    assert index["state"] == "ready" and index["model_calls"] == 0


def test_ambiguous_copies_need_explicit_choice_and_never_infer_deletion(
    config, policy, article_scope
):
    _, _, note = published(config, policy, article_scope)
    root = note.parent.parent
    a = root / "Archive/A.md"
    b = root / "Archive/B.md"
    shutil.copy2(note, a)
    shutil.copy2(note, b)
    note.unlink()
    with database.connect(config.state_root) as db:
        item = publication_reconcile_scan.scan(config, db, *article_scope[:2])["items"][0]
        assert item["state"] == "ambiguous" and item["suggested_path"] is None
        assert not item["can_mark_missing"]
        with pytest.raises(InvalidInput, match="deletion_review_required"):
            publication_reconcile.preview(config, db, *article_scope[:2], [choice(item, "missing")])
        assert (
            db.execute(
                "SELECT count(*) FROM vault_operations WHERE kind='publication_reconcile_batch'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("change", ["edited_target", "reappeared", "moved_after_preview"])
def test_external_change_after_preview_preserves_files_and_reports_failure(
    config, policy, article_scope, change
):
    _, pub, note = published(config, policy, article_scope)
    raw = note.read_bytes()
    target = note.parent.parent / "Archive/New.md"
    if change == "edited_target":
        note.rename(target)
    else:
        note.unlink()
    with database.connect(config.state_root) as db:
        item = publication_reconcile_scan.scan(config, db, *article_scope[:2])["items"][0]
        plan = publication_reconcile.preview(
            config,
            db,
            *article_scope[:2],
            [choice(item, "rebind" if change == "edited_target" else "missing")],
        )
        if change == "edited_target":
            target.write_bytes(raw + b"human edit")
        elif change == "reappeared":
            note.write_bytes(raw)
        else:
            target.write_bytes(raw)
        done = publication_reconcile.apply(config, db, *article_scope[:2], plan["operation_id"])
        assert done["state"] == "partial" and done["items"][0]["error"]
        assert publication_locations.get(db, "article", pub["publication_id"])["revision"] == 1
    assert (note if change == "reappeared" else target).exists()


def test_interruption_after_child_commit_resumes_without_duplicate_binding(
    config, policy, article_scope
):
    _, pub, note = published(config, policy, article_scope)
    note.rename(note.parent / "Renamed.md")
    original = publication_rebind.apply

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit("simulated process interruption")

    with database.connect(config.state_root) as db:
        item = publication_reconcile_scan.scan(config, db, *article_scope[:2])["items"][0]
        plan = publication_reconcile.preview(config, db, *article_scope[:2], [choice(item)])
        with (
            patch.object(publication_rebind, "apply", side_effect=interrupted),
            pytest.raises(SystemExit),
        ):
            publication_reconcile.apply(config, db, *article_scope[:2], plan["operation_id"])
        assert (
            publication_reconcile.get(db, *article_scope[:2], plan["operation_id"])[0]["state"]
            == "applying"
        )
        assert (
            publication_reconcile.apply(config, db, *article_scope[:2], plan["operation_id"])[
                "state"
            ]
            == "completed"
        )
        assert publication_locations.get(db, "article", pub["publication_id"])["revision"] == 2


def test_protected_subtree_is_excluded_before_read(config, policy, article_scope):
    _, _, note = published(config, policy, article_scope)
    root = note.parent.parent
    (root / "Protected").mkdir()
    (root / "Protected/secret.md").write_text("private")
    note.rename(note.parent / "Renamed.md")
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            (json.dumps({"include": [""], "exclude": ["Protected"]}), article_scope[1]),
        )
        activate(config, article_scope[1])
        from fiber_bundle.publication_file_io import read

        def protected_read(root, path):
            assert not path.startswith("Protected/")
            return read(root, path)

        with patch("fiber_bundle.publication_reconcile_scan.read", side_effect=protected_read):
            assert (
                len(publication_reconcile_scan.scan(config, db, *article_scope[:2])["items"]) == 1
            )


def test_batch_http_requires_scope_csrf_confirmation_and_rejects_duplicate_items(
    config, policy, article_scope, http
):
    _, _, note = published(config, policy, article_scope)
    note.rename(note.parent / "Renamed.md")
    scope = dict(zip(("client_id", "vault_id"), article_scope[:2], strict=True))
    prefix = "/admin/vault-publication-rebinds"
    assert (
        http.post(prefix + "/scan", json=scope, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    scanned = http.post(prefix + "/scan", json=scope)
    assert scanned.status_code == 200, scanned.text
    item = choice(scanned.json()["items"][0])
    assert (
        http.post(prefix + "/batch/preview", json={**scope, "items": [item, item]}).status_code
        == 409
    )
    response = http.post(prefix + "/batch/preview", json={**scope, "items": [item]})
    assert response.status_code == 200, response.text
    path = prefix + "/batch/" + response.json()["operation_id"]
    assert http.post(path + "/apply", json={**scope, "confirm": False}).status_code == 409
    assert http.get(path, params=scope).json()["state"] == "prepared"
    assert (
        http.post(path + "/apply", json={**scope, "confirm": True}).json()["state"] == "completed"
    )
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE client_vault_grants SET can_add=0 WHERE client_id=? AND vault_id=?",
            article_scope[:2],
        )
    assert http.post(path + "/apply", json={**scope, "confirm": True}).status_code == 404


def test_repair_works_with_stale_save_directory_binding_without_reactivating_policy(
    config, policy, article_scope
):
    _, _, note = published(config, policy, article_scope)
    root = note.parent.parent
    (root / "Archive").rename(root / "Archive-renamed")
    note.rename(root / "Archive-renamed/Moved.md")
    with database.connect(config.state_root) as db:
        before = tuple(db.execute("SELECT * FROM vault_save_policies").fetchone())
        assert not vault_save_policy.get(config, db, article_scope[1])["publication_ready"]
        scan = publication_reconcile_scan.scan(config, db, *article_scope[:2])
        assert scan["warnings"]
        packet = publication_reconcile.preview(
            config, db, *article_scope[:2], [choice(scan["items"][0])]
        )
        assert (
            publication_reconcile.apply(config, db, *article_scope[:2], packet["operation_id"])[
                "state"
            ]
            == "completed"
        )
        assert tuple(db.execute("SELECT * FROM vault_save_policies").fetchone()) == before
        assert not vault_save_policy.get(config, db, article_scope[1])["publication_ready"]


def test_policy_changed_after_preview_does_not_apply_rebind(config, policy, article_scope):
    _, pub, note = published(config, policy, article_scope)
    note.rename(note.parent / "Renamed.md")
    with database.connect(config.state_root) as db:
        item = publication_reconcile_scan.scan(config, db, *article_scope[:2])["items"][0]
        packet = publication_reconcile.preview(config, db, *article_scope[:2], [choice(item)])
        db.execute(
            "UPDATE vault_save_policies SET revision=revision+1 WHERE vault_id=?",
            (article_scope[1],),
        )
        done = publication_reconcile.apply(config, db, *article_scope[:2], packet["operation_id"])
        assert (
            done["state"] == "partial"
            and done["items"][0]["error"] == "layout_rebind_preview_changed"
        )
        assert publication_locations.get(db, "article", pub["publication_id"])["revision"] == 1


@pytest.mark.parametrize("partial", [False, True])
def test_whole_package_move_keeps_members_and_partial_package_cannot_be_marked_missing(
    config, policy, article_scope, partial
):
    published(config, policy, article_scope)
    root = config.project_root / "test vault/temp_vault1"
    identity = str(uuid4())
    files = {
        "note.md": b"# Synthetic package\n",
        "chapter.md": b"chapter body",
        "image.png": b"image bytes",
    }
    with database.connect(config.state_root) as db:
        location = publication_layout.prepare(
            config, db, "intake", identity, article_scope[1], files
        )
        publication_layout.publish(config, db, "intake", identity, article_scope[0], files)
        old = root / location["package_root"]
        if partial:
            (root / location["entry_note_path"]).unlink()
        else:
            old.rename(root / "Archive/WholePackage")
        item = next(
            i
            for i in publication_reconcile_scan.scan(config, db, *article_scope[:2])["items"]
            if i["publication_id"] == identity
        )
        if partial:
            assert item["state"] == "partial" and not item["can_mark_missing"]
            with pytest.raises(InvalidInput, match="deletion_review_required"):
                publication_reconcile.preview(
                    config, db, *article_scope[:2], [choice(item, "missing")]
                )
        else:
            packet = publication_reconcile.preview(config, db, *article_scope[:2], [choice(item)])
            assert (
                publication_reconcile.apply(config, db, *article_scope[:2], packet["operation_id"])[
                    "state"
                ]
                == "completed"
            )
            for name, raw in files.items():
                assert (root / "Archive/WholePackage" / name).read_bytes() == raw
