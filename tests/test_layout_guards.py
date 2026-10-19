"""Publication preflight and commit boundaries exercised against isolated real files."""

import pytest
from fastapi.testclient import TestClient
from test_article_publication import completed
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_publication_layout import activate

from fiber_bundle import (
    article_publication,
    article_reviews,
    auth,
    database,
    publication_layout,
    publication_locations,
    vault_save_policy,
)
from fiber_bundle.api import create_app
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.objects import digest


@pytest.mark.parametrize("change", ["revoke", "readonly", "directory", "symlink", "revision"])
def test_commit_rechecks_authority_and_frozen_directories(config, article_scope, change):
    client, vault, *_ = article_scope
    activate(config, vault)
    root = config.project_root / "test vault/temp_vault1"
    files = {"note.md": b"# Guarded\n![](images/a.png)", "images/a.png": b"image"}
    with database.connect(config.state_root) as db:
        row = publication_layout.prepare(config, db, "media", "guarded", vault, files)
        if change == "revoke":
            auth.set_grant(db, client, vault, query=True, original=True, add=False)
        elif change == "readonly":
            db.execute("UPDATE vaults SET read_only=1 WHERE id=?", (vault,))
        elif change in {"directory", "symlink"}:
            (root / "images").rename(root / "original-images")
            if change == "directory":
                (root / "images").mkdir()
            else:
                outside = config.project_root / "outside"
                outside.mkdir()
                (root / "images").symlink_to(outside, target_is_directory=True)
        else:
            activate(config, vault, archive_dir="Different")
        with pytest.raises((auth.Denied, IntegrityError, InvalidInput)):
            publication_layout.publish(config, db, "media", "guarded", client, files)
        assert publication_locations.get(db, "media", "guarded")["state"] != "published"
        assert not (root / row["entry_note_path"]).exists()
        assert (
            not list((config.project_root / "outside").glob("*")) if change == "symlink" else True
        )


def test_two_vaults_use_independent_rules_and_never_share_images(config, article_scope):
    client, first, *_ = article_scope
    activate(config, first)
    with database.connect(config.state_root) as db:
        second = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
        auth.set_grant(db, client, second, query=True, original=True, add=True)
        activate(config, second, inbox_dir="00-Inbox", archive_dir="领域", image_dir="attachments")
        files = {"note.md": "# 同一个标题\n![](images/a.png)".encode(), "images/a.png": b"image"}
        paths = []
        for vault in (first, second):
            publication_layout.prepare(config, db, "media", vault, vault, files)
            publication_layout.publish(config, db, "media", vault, client, files)
            paths.append(publication_locations.get(db, "media", vault)["entry_note_path"])
        assert paths == ["Inbox/同一个标题.md", "00-Inbox/同一个标题.md"]
        assert vault_save_policy.get(config, db, first)["settings"]["image_dir"] == "images"
        assert vault_save_policy.get(config, db, second)["settings"]["image_dir"] == "attachments"


def test_article_http_preflight_cancel_and_frozen_preview_have_no_vault_writes(
    config, policy, article_scope, http
):
    run = completed(config, policy, article_scope)
    activate(config, article_scope[1])
    root = config.project_root / "test vault/temp_vault1"
    with database.connect(config.state_root) as db:
        auth.set_grant(db, *article_scope[:2], query=True, original=True, add=True)
        packet = article_reviews.packet(config, db, run)
        review = article_reviews.record(
            config,
            db,
            run,
            {
                "manifest_sha256": packet["manifest_sha256"],
                "reason": "synthetic source inspected",
                "source_reviewed": True,
                "text_reviewed": True,
                "images_reviewed": True,
            },
        )
    url = f"/admin/vault-publications/article/{run}/preview"
    body = {k: review[k] for k in ("review_id", "manifest_sha256")}
    before = {p.relative_to(root): digest(p.read_bytes()) for p in root.rglob("*") if p.is_file()}
    with TestClient(
        create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1)
    ) as anonymous:
        assert anonymous.post(url, json=body).status_code in (401, 403)
    assert http.post(url, json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    response = http.post(url, json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["layout_mode"] == "obsidian_folders" and not preview["vault_write_performed"]
    assert not (root / preview["entry_note_path"]).exists()
    assert (
        http.post(
            f"/admin/vault-publications/article/{preview['publication_id']}/discard-preview"
        ).status_code
        == 200
    )
    with database.connect(config.state_root) as db:
        assert publication_locations.get(db, "article", preview["publication_id"]) is None
        assert db.execute("SELECT count(*) FROM article_publications").fetchone()[0] == 0
    assert before == {
        p.relative_to(root): digest(p.read_bytes()) for p in root.rglob("*") if p.is_file()
    }
    assert http.post(url, json=body).json()["entry_note_path"] == preview["entry_note_path"]
    activate(config, article_scope[1], inbox_dir="OtherInbox")
    with pytest.raises(IntegrityError, match="policy_changed"):
        article_publication.publish(config, article_scope[0], run)
    assert not (root / preview["entry_note_path"]).exists()
