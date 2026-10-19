import hashlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_migration import migration as migration
from test_note_migration import run as preflight
from test_note_migration_execution import query, run
from test_note_moves import published as published

from fiber_bundle import (
    file_exchange,
    moc_associations,
    moc_store,
    note_identity_store,
    note_migration_execution,
    note_transfer_links,
    note_transfer_stage,
)
from fiber_bundle.config import StorageError


@pytest.fixture
def linked(migration):
    root = migration["root"]
    (root / "assets").mkdir()
    (root / "assets/图.png").write_bytes(b"synthetic-binary-image\x00\xff")
    (root / "other.md").write_text("Human other note with shared image ![[assets/图.png]]")
    (root / "manual.md").write_text(
        "# 人工标题\nprivate-body-sentinel\n![[assets/图.png|300]]\n"
        '![保留标签](<assets/图.png> "保留标题")\n[[other|其他笔记]]\n'
        "`![[assets/图.png]]`\n<!-- ![[assets/图.png]] -->\n"
    )
    return migration


def prepare(config, scope, **extra):
    planned = preflight(config, scope)
    return run(config, scope, "prepare-reviewed", plan_id=planned["plan_id"], **extra)[
        "execution_id"
    ]


def test_attachments_copy_only_human_body_and_shared_source_preserved(config, linked):
    source = linked["root"] / "manual.md"
    original = source.read_bytes()
    asset = linked["root"] / "assets/图.png"
    shared = (asset.read_bytes(), asset.stat().st_ino, asset.stat().st_mtime_ns)
    other = (linked["root"] / "other.md").read_bytes()
    main = (config.state_root / "metadata.sqlite3").read_bytes()
    execution = prepare(config, linked)
    assert source.read_bytes() == original
    assert list(linked["destination"].rglob("*.md")) == []
    result = run(config, linked, "apply", execution_id=execution)
    assert result["status"] == "completed" and result["current_file_verified"]
    assert (
        result["attachment_policy"] == "copy_only_keep_source" and result["attachment_count"] == 1
    )
    assert result["unresolved_note_links"] == ["other.md"]
    target = linked["destination"] / "迁移/历史笔记.md"
    body = target.read_text()
    assert "# 人工标题\nprivate-body-sentinel\n" in body and "[[other|其他笔记]]" in body
    assert "`![[assets/图.png]]`" in body and "<!-- ![[assets/图.png]] -->" in body
    assert "|300]]" in body and '"保留标题")' in body
    assets = list(target.parent.glob("fiber-attachments-*/*.png"))
    assert len(assets) == 1 and assets[0].read_bytes() == shared[0]
    assert (asset.read_bytes(), asset.stat().st_ino, asset.stat().st_mtime_ns) == shared
    assert (linked["root"] / "other.md").read_bytes() == other
    assert list(source.parent.glob(".fiber-moved-*.md"))[0].read_bytes() == original
    assert query(config, linked, linked["target"])[0]["note_id"] == linked["identity"]
    assert query(config, linked, linked["source"]) == []
    assert (config.state_root / "metadata.sqlite3").read_bytes() == main
    target.unlink()
    assert not run(config, linked, "apply", execution_id=execution)["current_file_verified"]
    assert not target.exists()


def test_moc_manual_ownership_retained_and_only_explicit_target_added(config, linked):
    authorization = "Bearer " + linked["client"]["token"]
    src, dst, untouched = [str(uuid4()) for _ in range(3)]
    digest = hashlib.sha256((linked["root"] / "manual.md").read_bytes()).hexdigest()
    for vid, mid in [
        (linked["source"], src),
        (linked["target"], dst),
        (linked["target"], untouched),
    ]:
        moc_associations.run(
            config, authorization, vid, "create", mid, title="Human MOC", revision=0
        )
    for revision, owner in [(1, "manual"), (2, "system")]:
        moc_associations.run(
            config,
            authorization,
            linked["source"],
            "link",
            src,
            revision=revision,
            note_identity=linked["identity"],
            owner=owner,
            expected_sha256=digest,
        )
    source_moc_file = linked["root"] / "MOC.md"
    source_moc_file.write_text("# 人工地图\n[[manual]]\n")
    original = source_moc_file.read_bytes(), source_moc_file.stat().st_mtime_ns
    execution = prepare(config, linked, target_moc_id=dst, moc_revision=1)
    result = run(config, linked, "apply", execution_id=execution)
    assert moc_store.load(config, linked["source"], src).members[linked["identity"]] == ["manual"]
    assert moc_store.load(config, linked["target"], dst).members[linked["identity"]] == ["system"]
    assert moc_store.load(config, linked["target"], untouched).members == {}
    assert (source_moc_file.read_bytes(), source_moc_file.stat().st_mtime_ns) == original
    assert len(result["moc_projection_required"]) == 2


@pytest.mark.parametrize("change", ["edit", "replacement", "delete", "hardlink", "symlink"])
def test_attachment_change_rejects_before_claim_and_vault_write(config, linked, change):
    execution = prepare(config, linked)
    asset = linked["root"] / "assets/图.png"
    if change == "edit":
        asset.write_bytes(b"Changed human image")
    elif change == "replacement":
        copy = asset.with_name("copy.png")
        copy.write_bytes(asset.read_bytes())
        copy.replace(asset)
    elif change == "delete":
        asset.unlink()
    elif change == "hardlink":
        os.link(asset, asset.with_name("shared-hardlink.png"))
    else:
        outside = asset.with_name("other-image.png")
        outside.write_bytes(asset.read_bytes())
        asset.unlink()
        asset.symlink_to(outside)
    with pytest.raises((StorageError, OSError)):
        run(config, linked, "apply", execution_id=execution)
    assert run(config, linked, "status", execution_id=execution)["status"] == "prepared"
    assert not list(linked["destination"].rglob("*.md"))


@pytest.mark.parametrize("step", [1, 2, 3])
def test_real_exit_during_assets_note_or_original_move_resume_only_known_remaining_effects(
    config, linked, step
):
    execution = prepare(config, linked)
    token = config.project_root / "token.txt"
    token.write_text(linked["client"]["token"])
    script = """
import os,sys
from pathlib import Path
from fiber_bundle.config import load_config
from fiber_bundle import file_exchange,note_migration_execution as execute
root,source,target,execution,step=sys.argv[1:]
config=load_config(Path(root)/'config.json',Path(root))
old=file_exchange.rename
counter=0
def crash(*args,**kw):
    global counter
    old(*args,**kw)
    counter+=1
    if counter==int(step): os._exit(75)
file_exchange.rename=crash
execute.run(config,'Bearer '+(Path(root)/'token.txt').read_text(),source,target,
            'apply',execution_id=execution)
"""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(config.project_root),
            linked["source"],
            linked["target"],
            execution,
            str(step),
        ],
        env={**os.environ, "PYTHONPATH": str(Path(note_migration_execution.__file__).parents[1])},
        capture_output=True,
        timeout=20,
    )
    assert proc.returncode == 75, proc.stderr
    with pytest.raises(StorageError):
        query(config, linked, linked["target"])
    with patch.object(
        file_exchange, "rename", side_effect=AssertionError("reconcile cannot write")
    ):
        result = run(config, linked, "reconcile", execution_id=execution)
    if step < 3:
        assert result["status"] == "claimed" and not result["vault_write_performed"]
        result = run(config, linked, "resume", execution_id=execution)
    assert result["status"] == "completed"
    assert query(config, linked, linked["target"])[0]["note_id"] == linked["identity"]


@pytest.mark.parametrize("boundary", ["stage-before-record", "identity", "moc"])
def test_interruptions_preserve_owned_scope_and_reconcile_without_file_writes(
    config, linked, boundary
):
    extra = {}
    if boundary == "moc":
        mid = str(uuid4())
        moc_associations.run(
            config,
            "Bearer " + linked["client"]["token"],
            linked["target"],
            "create",
            mid,
            title="Target",
            revision=0,
        )
        extra = {"target_moc_id": mid, "moc_revision": 1}
    execution = prepare(config, linked, **extra)
    if boundary == "stage-before-record":
        module, name = note_transfer_stage, "save"
    elif boundary == "identity":
        module, name = note_identity_store, "transfer"
    else:
        module, name = moc_store, "save"
    with patch.object(module, name, side_effect=OSError("synthetic interruption")):
        with pytest.raises(OSError):
            run(config, linked, "apply", execution_id=execution)
    with patch.object(
        file_exchange, "rename", side_effect=AssertionError("no recovery file replay")
    ):
        result = run(config, linked, "reconcile", execution_id=execution)
    assert result["status"] == ("aborted" if boundary == "stage-before-record" else "completed")


@pytest.mark.parametrize(
    "body",
    [
        "![image](missing.png)",
        "<img src=assets/image.png>",
        "![image](../outside.png)",
        '<img srcset="one.png 1x">',
    ],
)
def test_missing_escaping_or_unsupported_media_refuses_preparation(config, linked, body):
    (linked["root"] / "manual.md").write_text(body)
    with pytest.raises(StorageError):
        prepare(config, linked)
    assert not (config.state_root / "note-migration-executions.sqlite3").exists()


def test_literal_src_fields_and_code_are_not_attachments(tmp_path):
    body = b"""human src="missing.png"\n```html\n<img src="missing.png">\n```\n"""
    result, copies, evidence, unresolved = note_transfer_links.prepare(
        tmp_path, "note.md", "note.md", "note:" + str(uuid4()), body
    )
    assert result == body and copies == {} and evidence == [] and unresolved == []


def test_incoming_suffixless_wikilinks_are_reported_without_changing_other_notes(config, linked):
    incoming = linked["root"] / "other.md"
    incoming.write_text("# Human incoming\n[[manual|保留名称]]\n[历史](manual.md)\n")
    original = incoming.read_bytes()
    execution = prepare(config, linked)
    result = run(config, linked, "apply", execution_id=execution)
    assert result["incoming_note_links"] == [
        {"note_path": "other.md", "line": 2, "target": "manual|保留名称"},
        {"note_path": "other.md", "line": 3, "target": "manual.md"},
    ]
    assert incoming.read_bytes() == original
