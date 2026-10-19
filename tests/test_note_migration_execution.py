import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_migration import migration as migration
from test_note_migration import run as preflight
from test_note_moves import published as published

from fiber_bundle import (
    auth,
    database,
    file_exchange,
    note_identity_store,
    search,
)
from fiber_bundle import (
    note_migration_execution as execute,
)
from fiber_bundle import (
    note_migration_store as journal,
)
from fiber_bundle.config import StorageError


def run(config, scope, action, **args):
    return execute.run(
        config,
        "Bearer " + scope["client"]["token"],
        scope["source"],
        scope["target"],
        action,
        **args,
    )


def prepare(config, scope):
    plan = preflight(config, scope)
    return run(config, scope, "prepare", plan_id=plan["plan_id"])["execution_id"]


def query(config, scope, vid):
    db = database.connect(config.state_root)
    try:
        return search.search(config, db, scope["client"]["id"], "sentinel", [vid])["results"]
    finally:
        db.close()


def test_explicit_intent_moves_bytes_and_identity_and_retains_audit(config, migration):
    src = migration["root"] / "manual.md"
    dst = migration["destination"] / "迁移/历史笔记.md"
    original = src.read_bytes(), src.stat().st_ino, src.stat().st_mtime_ns
    main = (config.state_root / "metadata.sqlite3").read_bytes()
    execution = prepare(config, migration)
    assert src.exists() and not dst.exists()
    result = run(config, migration, "apply", execution_id=execution)
    assert result["status"] == "completed" and result["vault_write_performed"]
    assert (
        not src.exists()
        and (dst.read_bytes(), dst.stat().st_ino, dst.stat().st_mtime_ns) == original
    )
    assert query(config, migration, migration["source"]) == []
    assert query(config, migration, migration["target"])[0]["note_id"] == migration["identity"]
    assert note_identity_store.records(config, migration["source"]) == []
    assert note_identity_store.records(config, migration["target"])[0].revision == 2
    with note_identity_store.connection(config) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 3
    assert (config.state_root / "metadata.sqlite3").read_bytes() == main
    repeat = run(config, migration, "apply", execution_id=execution)
    assert not repeat["vault_write_performed"] and not repeat["current_file_verified"]
    dst.unlink()
    assert run(config, migration, "apply", execution_id=execution)["status"] == "completed"
    assert not dst.exists()  # Historical completion never rebuilds deletion.


@pytest.mark.parametrize("end", ["source", "target"])
@pytest.mark.parametrize("permission", ["query", "add"])
def test_execution_authorizes_both_ends_before_reading_intent(config, migration, end, permission):
    execution = prepare(config, migration)
    db = database.connect(config.state_root)
    auth.set_grant(
        db,
        migration["client"]["id"],
        migration[end],
        query=permission != "query",
        original=False,
        add=permission != "add",
    )
    db.close()
    with patch.object(journal, "load", side_effect=AssertionError("unauthorized read")):
        for action in ("apply", "reconcile", "status"):
            with pytest.raises(auth.Denied):
                run(config, migration, action, execution_id=execution)


@pytest.mark.parametrize("change", ["source", "target", "other-source", "other-target"])
def test_stale_intent_does_not_claim_or_move(config, migration, change):
    execution = prepare(config, migration)
    root = migration["root"] if change in {"source", "other-source"} else migration["destination"]
    name = (
        "manual.md"
        if change == "source"
        else "迁移/历史笔记.md"
        if change == "target"
        else "new.md"
    )
    (root / name).write_text("Human edited body")
    with pytest.raises(StorageError):
        run(config, migration, "apply", execution_id=execution)
    assert run(config, migration, "status", execution_id=execution)["status"] == "prepared"


def test_dependency_flags_cannot_be_treated_as_write_permission(config, migration):
    (migration["root"] / "manual.md").write_text("[[another note]]")
    plan = preflight(config, migration)
    with pytest.raises(StorageError):
        run(config, migration, "prepare", plan_id=plan["plan_id"])
    assert not (config.state_root / "note-migration-executions.sqlite3").exists()


def test_failed_identity_audit_is_atomic_and_reconciled_without_file_replay(config, migration):
    execution = prepare(config, migration)
    with note_identity_store.connection(config, write=True) as db:
        db.execute(
            "CREATE TRIGGER fail_migration BEFORE INSERT ON events WHEN NEW.revision=2 "
            "BEGIN SELECT RAISE(ABORT,'synthetic audit failure'); END"
        )
    with pytest.raises(sqlite3.Error):
        run(config, migration, "apply", execution_id=execution)
    assert note_identity_store.records(config, migration["source"])[0].revision == 1
    assert note_identity_store.records(config, migration["target"]) == []
    for vid in (migration["source"], migration["target"]):
        with pytest.raises(StorageError):
            query(config, migration, vid)
    with pytest.raises(StorageError):
        run(config, migration, "apply", execution_id=execution)
    with note_identity_store.connection(config, write=True) as db:
        db.execute("DROP TRIGGER fail_migration")
    with patch.object(file_exchange, "rename", side_effect=AssertionError("must not replay")):
        result = run(config, migration, "reconcile", execution_id=execution)
    assert result["status"] == "completed" and not result["vault_write_performed"]
    assert result["current_file_verified"]
    assert query(config, migration, migration["target"])[0]["note_id"] == migration["identity"]


@pytest.mark.parametrize("boundary", ["claim", "rename", "identity"])
def test_real_process_exit_reconciliation(config, migration, boundary):
    execution = prepare(config, migration)
    token = config.project_root / "token.txt"
    token.write_text(migration["client"]["token"])
    script = """
import os,sys
from pathlib import Path
from fiber_bundle.config import load_config
from fiber_bundle import note_migration_execution as execute, note_migration_store as journal
from fiber_bundle import file_exchange, note_identity_store
root,source,target,intent,boundary=sys.argv[1:]
config=load_config(Path(root)/'config.json',Path(root))
choices={'claim':(journal,'transition'), 'rename':(file_exchange,'rename'),
         'identity':(note_identity_store,'transfer')}
module,name=choices[boundary]
original=getattr(module,name)
def crash(*args,**kwargs):
    result=original(*args,**kwargs)
    os._exit(73)
setattr(module,name,crash)
execute.run(config,'Bearer '+(Path(root)/'token.txt').read_text(),source,target,
            'apply',execution_id=intent)
"""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(config.project_root),
            migration["source"],
            migration["target"],
            execution,
            boundary,
        ],
        env={**os.environ, "PYTHONPATH": str(Path(execute.__file__).parents[1])},
        capture_output=True,
        timeout=20,
    )
    assert proc.returncode == 73, proc.stderr
    assert run(config, migration, "status", execution_id=execution)["requires_reconciliation"]
    with patch.object(file_exchange, "rename", side_effect=AssertionError("no replay")):
        result = run(config, migration, "reconcile", execution_id=execution)
    assert result["status"] == ("aborted" if boundary == "claim" else "completed")
    if boundary == "claim":
        assert (migration["root"] / "manual.md").exists()
        with pytest.raises(StorageError):
            run(config, migration, "apply", execution_id=execution)
    else:
        assert query(config, migration, migration["target"])[0]["note_id"] == migration["identity"]


def test_human_edit_after_move_is_preserved_and_requires_manual_reconciliation(config, migration):
    execution = prepare(config, migration)
    with patch.object(
        note_identity_store, "transfer", side_effect=OSError("synthetic interruption")
    ):
        with pytest.raises(OSError):
            run(config, migration, "apply", execution_id=execution)
    target = migration["destination"] / "迁移/历史笔记.md"
    target.write_text("Human edited after migration move")
    with pytest.raises(StorageError):
        run(config, migration, "reconcile", execution_id=execution)
    assert target.read_text() == "Human edited after migration move"
    assert run(config, migration, "status", execution_id=execution)["status"] == "claimed"


def test_tampered_intent_and_cross_client_access_are_rejected(config, migration):
    execution = prepare(config, migration)
    with journal.connection(config, write=True) as db:
        raw = db.execute("SELECT intent_json FROM intents").fetchone()[0]
        value = json.loads(raw)
        value["plan"]["target_path"] = "another.md"
        db.execute("UPDATE intents SET intent_json=?", (json.dumps(value),))
    with pytest.raises(StorageError):
        run(config, migration, "apply", execution_id=execution)
    assert (migration["root"] / "manual.md").exists()
