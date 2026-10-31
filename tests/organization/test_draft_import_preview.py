import json
import shutil
from uuid import uuid4

import pytest

from fiber_bundle import database, vault_save_policy
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import draft_import_files as files
from fiber_bundle.organization import draft_import_preview as preview
from fiber_bundle.vault_save_contract import SaveSettings


@pytest.fixture
def draft(config, scope, package, tmp_path):
    cid, vid, root = scope
    source = tmp_path / "external-draft"
    shutil.copytree(package, source)
    shutil.rmtree(package)
    with database.connect(config.state_root) as db:
        s = SaveSettings(
            policy_version=2,
            layout_mode="obsidian_folders",
            image_dir="Attachments/images",
            original_dir="Attachments/pristin",
        )
        p = vault_save_policy.preview(config, db, vid, s, 0)
        vault_save_policy.save(config, db, vid, p["preview_id"], 0)
        p = vault_save_policy.preview(config, db, vid, s, 1, enable_publication=True)
        vault_save_policy.save(config, db, vid, p["preview_id"], 1)
    return cid, vid, root, source


def prepared(config, draft):
    cid, vid, _, source = draft
    with database.connect(config.state_root) as db:
        target = preview.target(config, db, cid, vid)
    return preview.prepare(
        config,
        {"id": str(uuid4()), "client_id": cid, "vault_id": vid, "source_path": str(source)},
        target,
    )


def test_preview_copies_nothing_and_preserves_source(config, draft):
    before = {str(p): p.read_bytes() for p in draft[3].rglob("*") if p.is_file()}
    t = prepared(config, draft)
    with database.connect(config.state_root) as db:
        t = preview.finish(config, db, t)
    assert t["state"] == "preview" and t["preview_sha256"]
    assert t["relative_path"] == "Book/external-draft"
    assert not (draft[2] / t["relative_path"]).exists()
    assert t["package"]["knowledge_verified"] is False
    assert before == {str(p): p.read_bytes() for p in draft[3].rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "damage", ["missing", "changed", "traversal", "symlink", "extra", "protocol"]
)
def test_invalid_source_rejected(config, draft, damage):
    p = draft[3]
    path = p / "assets/test.png"
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        path.write_bytes(b"bad")
    elif damage == "symlink":
        path.unlink()
        path.symlink_to("/etc/hosts")
    elif damage == "extra":
        (p / "secret.txt").write_text("not in package")
    else:
        m = json.loads((p / "manifest.json").read_text())
        if damage == "traversal":
            m["integrity"]["files"]["../other"] = "a" * 64
        else:
            m["schema_version"] = "unknown"
        (p / "manifest.json").write_text(json.dumps(m))
    with pytest.raises((IntegrityError, InvalidInput, OSError)):
        prepared(config, draft)


def test_source_modified_after_preview_rejected(config, draft):
    t = prepared(config, draft)
    (draft[3] / "SKILL.md").write_text("edited")
    with pytest.raises(IntegrityError, match="source_changed"):
        files.recheck(t)


def test_conflict_no_overwrite(config, draft):
    t = prepared(config, draft)
    destination = draft[2] / t["relative_path"]
    destination.mkdir()
    (destination / "human.md").write_text("keep")
    with (
        database.connect(config.state_root) as db,
        pytest.raises(InvalidInput, match="destination_exists"),
    ):
        preview.finish(config, db, t)
    assert (destination / "human.md").read_text() == "keep"


def test_low_space_rejected(config, draft, monkeypatch):
    t = prepared(config, draft)
    monkeypatch.setattr(
        preview.shutil, "disk_usage", lambda _: __import__("collections").namedtuple("D", "free")(0)
    )
    with (
        database.connect(config.state_root) as db,
        pytest.raises(InvalidInput, match="insufficient_space"),
    ):
        preview.finish(config, db, t)


def test_protected_path_rejected_before_metadata(monkeypatch):
    from fiber_bundle import vault_scope

    monkeypatch.setattr(
        files.Path, "resolve", lambda *a, **kw: pytest.fail("protected path inspected")
    )
    with pytest.raises(InvalidInput, match="protected"):
        files.source_root(str(vault_scope.PROTECTED / "draft"))
