import hashlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_moves import published as published

from fiber_bundle import (
    article_publication,
    auth,
    database,
    file_exchange,
    note_identities,
    note_identity_store,
    search,
)
from fiber_bundle import note_migration_execution as execute
from fiber_bundle.config import StorageError


@pytest.fixture
def transfer(config, published, article_scope):
    db, result, note, rid = published
    client = auth.create_client(db, "migration owner", False)
    target = db.execute("SELECT * FROM vaults WHERE name='supplier'").fetchone()
    for vid in (article_scope[1], target["id"]):
        auth.set_grant(db, client["id"], vid, query=True, original=False, add=True)
    destination = config.project_root / target["relative_path"]
    (destination / "迁移").mkdir()
    authorization = "Bearer " + client["token"]
    registration = note_identities.run(
        config,
        authorization,
        article_scope[1],
        "track",
        result["note_path"],
        hashlib.sha256(note.read_bytes()).hexdigest(),
    )
    return {
        "client": client,
        "source": article_scope[1],
        "target": target["id"],
        "root": note.parent.parent,
        "note": note,
        "destination": destination,
        "registration": registration,
        "result": result,
        "run_id": rid,
        "original_client": article_scope[0],
    }


def run(config, scope, action, **extra):
    args = {}
    if action == "prepare-publication":
        args = {
            "note_identity": scope["registration"]["note_identity"],
            "revision": 1,
            "source_path": scope["note"].relative_to(scope["root"]).as_posix(),
            "target_path": "迁移/article/note.md",
            "expected_sha256": hashlib.sha256(scope["note"].read_bytes()).hexdigest(),
        }
    args.update(extra)
    return execute.run(
        config,
        "Bearer " + scope["client"]["token"],
        scope["source"],
        scope["target"],
        action,
        **args,
    )


def hits(config, scope, vid):
    db = database.connect(config.state_root)
    try:
        return search.search(config, db, scope["client"]["id"], "Synthetic", [vid])["results"]
    finally:
        db.close()


def test_published_bundle_preserves_body_assets_provenance_and_original_permissions(
    config, transfer
):
    scope = transfer
    original = {
        p.relative_to(scope["note"].parent).as_posix(): (
            p.read_bytes(),
            p.stat().st_ino,
            p.stat().st_mtime_ns,
        )
        for p in scope["note"].parent.rglob("*")
        if p.is_file()
    }
    execution = run(config, scope, "prepare-publication")["execution_id"]
    assert scope["note"].exists()
    result = run(config, scope, "apply", execution_id=execution)
    assert result["status"] == "completed" and result["current_file_verified"]
    for name, (data, inode, mtime) in original.items():
        assert (scope["destination"] / "迁移/article" / name).read_bytes() == data
        if name != "note.md":
            p = scope["note"].parent / name
            assert (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) == (data, inode, mtime)
    assert not scope["note"].exists()
    assert hits(config, scope, scope["source"]) == []
    hit = hits(config, scope, scope["target"])[0]
    assert hit["note_id"] == scope["registration"]["note_identity"]
    assert hit["source_kind"] == "published_article"
    assert hit["source_version_id"] == scope["result"]["source_version_id"]
    assert hit["originals"] == []
    db = database.connect(config.state_root)
    auth.set_grant(db, scope["client"]["id"], scope["target"], query=True, original=True, add=True)
    assert hits(config, scope, scope["target"])[0]["originals"] == []
    auth.set_grant(db, scope["client"]["id"], scope["source"], query=True, original=True, add=True)
    originals = hits(config, scope, scope["target"])[0]["originals"]
    assert originals and all(f"/vaults/{scope['source']}/" in a["url"] for a in originals)
    before = (config.state_root / "metadata.sqlite3").read_bytes()
    with pytest.raises(StorageError, match="original-run replay"):
        article_publication.publish(config, scope["original_client"], scope["run_id"])
    assert (config.state_root / "metadata.sqlite3").read_bytes() == before
    db.close()
    target = scope["destination"] / "迁移/article/note.md"
    target.unlink()
    assert not run(config, scope, "apply", execution_id=execution)["current_file_verified"]
    assert not target.exists()


def test_edited_note_and_outgoing_note_links_remain_user_revision(config, transfer):
    transfer["note"].write_text("# Synthetic human revision\n[[other|keep]]\n")
    execution = run(config, transfer, "prepare-publication")["execution_id"]
    result = run(config, transfer, "apply", execution_id=execution)
    assert result["unresolved_note_links"] == [transfer["note"].parent.name + "/other"]
    assert (
        (transfer["destination"] / "迁移/article/note.md").read_text().endswith("[[other|keep]]\n")
    )
    assert hits(config, transfer, transfer["target"])[0]["source_kind"] == "user_revision"


@pytest.mark.parametrize("change", ["note", "asset", "target", "grant"])
def test_changed_evidence_never_claims_or_moves(config, transfer, change):
    execution = run(config, transfer, "prepare-publication")["execution_id"]
    if change == "note":
        transfer["note"].write_text("Synthetic changed")
    elif change == "asset":
        next(transfer["note"].parent.glob("images/*")).write_bytes(b"changed image")
    elif change == "target":
        (transfer["destination"] / "迁移/article").mkdir()
    else:
        db = database.connect(config.state_root)
        auth.set_grant(
            db, transfer["client"]["id"], transfer["target"], query=False, original=False, add=True
        )
        db.close()
    with pytest.raises((StorageError, auth.Denied)):
        run(config, transfer, "apply", execution_id=execution)
    assert transfer["note"].exists()


@pytest.mark.parametrize("boundary", ["bundle", "source", "ledger", "identity"])
def test_real_exit_requires_reconciliation_and_only_owned_resume(config, transfer, boundary):
    execution = run(config, transfer, "prepare-publication")["execution_id"]
    # Real process exit at the durable file and metadata boundaries.
    script = """
import os,sys
from pathlib import Path
from fiber_bundle.config import load_config
from fiber_bundle import file_exchange, publication_transfer, note_identity_store
from fiber_bundle.note_migration_execution import run
config=load_config(Path(sys.argv[1]),Path(sys.argv[2]))
boundary=sys.argv[7]
if boundary in {"bundle","source"}:
    original=file_exchange.rename
    count=0
    def exited(*a,**k):
        global count
        original(*a,**k); count+=1
        if count==({"bundle":1,"source":2}[boundary]): os._exit(76)
    file_exchange.rename=exited
else:
    owner=publication_transfer if boundary=="ledger" else note_identity_store
    name="relocate" if boundary=="ledger" else "transfer"
    original=getattr(owner,name)
    def exited(*a,**k):
        original(*a,**k); os._exit(76)
    setattr(owner,name,exited)
run(config,sys.argv[3],sys.argv[4],sys.argv[5],"apply",execution_id=sys.argv[6])
"""
    config_path = config.project_root / "config.json"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(config_path),
            str(config.project_root),
            "Bearer " + transfer["client"]["token"],
            transfer["source"],
            transfer["target"],
            execution,
            boundary,
        ],
        env={**os.environ, "PYTHONPATH": str(Path(execute.__file__).parents[1])},
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 76, result.stderr.decode()
    with pytest.raises(StorageError):
        hits(config, transfer, transfer["target"])
    with patch.object(file_exchange, "rename", side_effect=AssertionError("no file replay")):
        outcome = run(config, transfer, "reconcile", execution_id=execution)
    if boundary == "bundle":
        assert outcome["status"] == "claimed"
        outcome = run(config, transfer, "resume", execution_id=execution)
    assert outcome["status"] == "completed"
    assert (
        hits(config, transfer, transfer["target"])[0]["note_id"]
        == transfer["registration"]["note_identity"]
    )
    assert note_identity_store.records(config, transfer["source"]) == []
