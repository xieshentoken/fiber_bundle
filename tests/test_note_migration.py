import hashlib
import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_identities import track
from test_note_moves import published as published

from fiber_bundle import (
    auth,
    database,
    moc_associations,
    note_identity_store,
    note_inventory,
    note_migration,
    note_migration_plans,
)
from fiber_bundle.config import StorageError
from fiber_bundle.inputs import canonical


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def migration(config, identity_scope):
    client, vid, root = identity_scope
    registered = track(config, identity_scope)
    db = database.connect(config.state_root)
    target = db.execute("SELECT * FROM vaults WHERE name='supplier'").fetchone()
    auth.set_grant(db, client["id"], target["id"], query=True, original=False, add=True)
    db.close()
    destination = config.project_root / target["relative_path"]
    (destination / "迁移").mkdir()
    return {
        "client": client,
        "source": vid,
        "target": target["id"],
        "root": root,
        "destination": destination,
        "identity": registered["note_identity"],
    }


def run(config, scope, action="plan", **extra):
    args = (
        {}
        if action != "plan"
        else {
            "note_identity": scope["identity"],
            "revision": 1,
            "source_path": "manual.md",
            "target_path": "迁移/历史笔记.md",
            "expected_sha256": sha(scope["root"] / "manual.md"),
        }
    )
    args.update(extra)
    return note_migration.run(
        config,
        "Bearer " + scope["client"]["token"],
        scope["source"],
        scope["target"],
        action,
        **args,
    )


def path(config, scope, plan):
    return note_migration_plans.path_for(
        config, scope["client"]["id"], scope["source"], scope["target"], plan["plan_id"]
    )


def files(root):
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_freeze_check_and_repeat_preserve_bodies_and_all_existing_ledgers(config, migration):
    before = files(migration["root"]), files(migration["destination"])
    registration = (config.state_root / "note-identities.sqlite3").read_bytes()
    main = (config.state_root / "metadata.sqlite3").read_bytes()
    planned = run(config, migration)
    assert planned["status"] == "planned" and planned["metadata_only"]
    assert planned["plan"]["note_identity"] == migration["identity"]
    assert planned["plan"]["review_flags"] == []
    assert not planned["execution_available"] and not planned["write_authorization"]
    assert not planned["vault_write_performed"]
    stored = path(config, migration, planned)
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert stat.S_IMODE(stored.parent.stat().st_mode) == 0o700
    assert b"private-body-sentinel" not in stored.read_bytes()
    assert migration["client"]["token"].encode() not in stored.read_bytes()
    assert str(migration["root"]).encode() not in stored.read_bytes()
    saved = stored.read_bytes(), stored.stat().st_mtime_ns
    assert run(config, migration) == planned
    checked = run(config, migration, "check", plan_id=planned["plan_id"])
    assert checked["status"] == "match" and checked["changed_fields"] == []
    assert (stored.read_bytes(), stored.stat().st_mtime_ns) == saved
    assert (files(migration["root"]), files(migration["destination"])) == before
    assert (config.state_root / "note-identities.sqlite3").read_bytes() == registration
    assert (config.state_root / "metadata.sqlite3").read_bytes() == main
    assert not (config.state_root / "moc-associations.sqlite3").exists()


@pytest.mark.parametrize("end", ["source", "target"])
@pytest.mark.parametrize("permission", ["query", "add", "client"])
def test_both_permissions_are_checked_before_any_inventory_or_plan_read(
    config, migration, end, permission
):
    planned = run(config, migration)
    db = database.connect(config.state_root)
    if permission == "client":
        db.execute("UPDATE clients SET active=0 WHERE id=?", (migration["client"]["id"],))
    else:
        auth.set_grant(
            db,
            migration["client"]["id"],
            migration[end],
            query=permission != "query",
            original=False,
            add=permission != "add",
        )
    db.close()
    with (
        patch.object(note_inventory, "read", side_effect=AssertionError("unauthorized scan")),
        patch.object(note_migration_plans, "load", side_effect=AssertionError("unauthorized plan")),
    ):
        for action, args in [("plan", {}), ("check", {"plan_id": planned["plan_id"]})]:
            with pytest.raises(auth.Denied):
                run(config, migration, action, **args)


@pytest.mark.parametrize(
    "change",
    [
        "source-edit",
        "source-delete",
        "source-rename",
        "source-copy",
        "target-occupied",
        "target-parent",
        "source-other",
        "target-other",
        "revision",
    ],
)
def test_fixed_plan_detects_current_conflicts_without_replan_or_repair(config, migration, change):
    planned = run(config, migration)
    source = migration["root"] / "manual.md"
    if change == "source-edit":
        source.write_text("Human new body")
    elif change == "source-delete":
        source.unlink()
    elif change == "source-rename":
        source.rename(source.with_name("renamed.md"))
    elif change == "source-copy":
        other = source.with_name("copy.md")
        other.write_bytes(source.read_bytes())
        other.replace(source)
    elif change == "target-occupied":
        (migration["destination"] / "迁移/历史笔记.md").write_text("Existing target")
    elif change == "target-parent":
        (migration["destination"] / "迁移").rename(migration["destination"] / "previous")
        (migration["destination"] / "迁移").mkdir()
    elif change in {"source-other", "target-other"}:
        base = migration["root"] if change == "source-other" else migration["destination"]
        (base / "other.md").write_text("# Unrelated current note")
    else:
        current = note_identity_store.records(config, migration["source"])[0]
        note_identity_store.save(
            config,
            migration["source"],
            migration["client"]["id"],
            replace(current, revision=2),
            expected_revision=1,
        )
    before = (
        files(migration["root"]),
        files(migration["destination"]),
        path(config, migration, planned).read_bytes(),
    )
    result = run(config, migration, "check", plan_id=planned["plan_id"])
    assert result["status"] == "conflict" and result["changed_fields"]
    assert result["plan_id"] == planned["plan_id"]
    assert not result["vault_write_performed"] and not result["write_authorization"]
    assert (
        files(migration["root"]),
        files(migration["destination"]),
        path(config, migration, planned).read_bytes(),
    ) == before
    assert len(list(path(config, migration, planned).parent.iterdir())) == 1


@pytest.mark.parametrize("end", ["source", "target"])
def test_root_replacement_refuses_original_plan(config, migration, end):
    planned = run(config, migration)
    root = migration["root"] if end == "source" else migration["destination"]
    root.rename(root.with_name(root.name + "-old"))
    root.mkdir()
    if end == "target":
        (root / "迁移").mkdir()
    with pytest.raises(StorageError):
        run(config, migration, "check", plan_id=planned["plan_id"])
    assert not (root / "manual.md").exists()


def test_link_and_moc_flags_are_conservative_review_hints_not_execution(config, migration):
    source = migration["root"] / "manual.md"
    source.write_text(
        "---\ntype: moc\n---\n# Human\n![[shared.png]]\n[Link](other.md)\n"
        "<!-- fiber-moc: manual -->\n"
    )
    (migration["root"] / "incoming.md").write_text("[[manual]]")
    (migration["destination"] / "other.md").write_text("<img src='shared.png'>")
    mid = str(uuid4())
    token = "Bearer " + migration["client"]["token"]
    moc_associations.run(
        config, token, migration["source"], "create", mid, title="Review MOC", revision=0
    )
    moc_associations.run(
        config,
        token,
        migration["source"],
        "link",
        mid,
        revision=1,
        note_identity=migration["identity"],
        owner="manual",
        expected_sha256=sha(source),
    )
    result = run(config, migration)
    assert set(result["plan"]["review_flags"]) == {
        "source_links_need_review",
        "other_source_links_need_review",
        "target_links_need_review",
        "source_moc_membership",
        "moc_document_needs_review",
    }
    assert not result["execution_available"]
    checked = run(config, migration, "check", plan_id=result["plan_id"])
    assert checked["status"] == "match" and checked["plan"]["review_flags"]
    moc_associations.run(
        config,
        token,
        migration["source"],
        "link",
        mid,
        revision=2,
        note_identity=migration["identity"],
        owner="system",
        expected_sha256=sha(source),
    )
    result = run(config, migration, "check", plan_id=result["plan_id"])
    assert result["status"] == "conflict" and "source_mocs_sha256" in result["changed_fields"]


@pytest.mark.parametrize(
    "kind",
    [
        "existing",
        "directory",
        "symlink",
        "hardlink",
        "fifo",
        "hidden",
        "escape",
        "publication",
        "missing-parent",
    ],
)
def test_target_is_an_absent_safe_slot_and_parent_is_never_created(config, migration, kind):
    target = migration["destination"] / "迁移/历史笔记.md"
    relative = "迁移/历史笔记.md"
    if kind == "existing":
        target.write_bytes((migration["root"] / "manual.md").read_bytes())
    elif kind == "directory":
        target.mkdir()
    elif kind == "symlink":
        target.symlink_to(migration["root"] / "manual.md")
    elif kind == "hardlink":
        os.link(migration["root"] / "manual.md", target)
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "hidden":
        relative = ".hidden/target.md"
    elif kind == "escape":
        relative = "../outside.md"
    elif kind == "publication":
        (target.parent / ".fiber-publication.json").write_text("{}")
    else:
        relative = "uncreated/target.md"
    with pytest.raises((StorageError, OSError)):
        run(config, migration, target_path=relative)
    assert not (migration["destination"] / "uncreated").exists()
    assert not (config.state_root / "note-migrations").exists()


@pytest.mark.parametrize(
    "arg",
    [
        {"revision": True},
        {"revision": 2},
        {"note_identity": "path:manual.md"},
        {"note_identity": "publication:" + str(uuid4())},
        {"source_path": "missing.md"},
        {"expected_sha256": "0" * 64},
        {"source_path": ".private.md"},
        {"plan_id": "0" * 64},
    ],
)
def test_plan_inputs_must_identify_current_registered_historical_note(config, migration, arg):
    with pytest.raises(StorageError):
        run(config, migration, **arg)
    assert not (config.state_root / "note-migrations").exists()


def test_same_vault_and_target_registration_conflicts_are_rejected(config, migration):
    with pytest.raises(StorageError):
        note_migration.run(
            config,
            "Bearer " + migration["client"]["token"],
            migration["source"],
            migration["source"],
            "plan",
        )
    source = note_identity_store.records(config, migration["source"])[0]
    info = migration["destination"].stat()
    note_identity_store.save(
        config,
        migration["target"],
        migration["client"]["id"],
        replace(source, relative_path="absent.md", vault_key=(info.st_dev, info.st_ino)),
    )
    with pytest.raises(StorageError):
        run(config, migration)


@pytest.mark.parametrize("point", ["observation", "after-save", "revocation"])
def test_planning_races_do_not_deliver_success_or_write_vaults(config, migration, point):
    original = note_migration.observe
    save = note_migration_plans.save
    calls = 0

    def observe(*args, **kwargs):
        nonlocal calls
        value = original(*args, **kwargs)
        calls += 1
        if (calls == 1 and point == "observation") or (calls == 2 and point == "revocation"):
            if point == "revocation":
                db = database.connect(config.state_root)
                auth.set_grant(
                    db,
                    migration["client"]["id"],
                    migration["target"],
                    query=False,
                    original=False,
                    add=True,
                )
                db.close()
            else:
                (migration["root"] / "manual.md").write_text("Human concurrent change")
        return value

    def saved(*args, **kwargs):
        identity = save(*args, **kwargs)
        if point == "after-save":
            (migration["root"] / "manual.md").write_text("Human concurrent change")
        return identity

    with (
        patch.object(note_migration, "observe", side_effect=observe),
        patch.object(note_migration_plans, "save", side_effect=saved),
        pytest.raises((StorageError, auth.Denied)),
    ):
        run(config, migration)
    assert not (migration["destination"] / "迁移/历史笔记.md").exists()
    root = note_migration_plans.directory(
        config, migration["client"]["id"], migration["source"], migration["target"]
    )
    assert (len(list(root.iterdir())) if root.exists() else 0) == (
        1 if point == "after-save" else 0
    )


@pytest.mark.parametrize(
    "bad", ["extra", "schema", "revision", "birth", "inode", "digest", "flags", "scope", "path"]
)
def test_rehashed_malformed_contract_is_not_accepted(config, migration, bad):
    result = run(config, migration)
    value = deepcopy(result["plan"])
    if bad == "extra":
        value["extra"] = "sensitive"
    elif bad == "schema":
        value["schema"] = True
    elif bad == "revision":
        value["revision"] = True
    elif bad == "birth":
        value["source_file_key"][2] = "nan"
    elif bad == "inode":
        value["target_parent_key"][1] = True
    elif bad == "digest":
        value["source_inventory_sha256"] = "x"
    elif bad == "flags":
        value["review_flags"] = ["arbitrary"]
    elif bad == "scope":
        value["target_vault_id"] = migration["source"]
    else:
        value["target_path"] = "../escape.md"
    raw = canonical(value).encode()
    identifier = hashlib.sha256(raw).hexdigest()
    target = note_migration_plans.path_for(
        config, migration["client"]["id"], migration["source"], migration["target"], identifier
    )
    target.write_bytes(raw)
    with pytest.raises(StorageError):
        run(config, migration, "check", plan_id=identifier)


@pytest.mark.parametrize("kind", ["tampered", "symlink", "hardlink", "fifo", "oversize"])
def test_unsafe_plan_files_are_refused_without_repair(config, migration, kind):
    result = run(config, migration)
    target = path(config, migration, result)
    raw = target.read_bytes()
    if kind == "tampered":
        target.write_bytes(raw + b" ")
    else:
        target.unlink()
        outside = config.project_root / "external-plan.json"
        outside.write_bytes(raw)
        if kind == "symlink":
            target.symlink_to(outside)
        elif kind == "hardlink":
            os.link(outside, target)
        elif kind == "fifo":
            os.mkfifo(target)
        else:
            target.write_bytes(b"x" * (note_migration_plans.MAX_BYTES + 1))
    with pytest.raises(StorageError):
        run(config, migration, "check", plan_id=result["plan_id"])


def test_other_owner_and_reversed_endpoints_cannot_load_known_plan(config, migration):
    planned = run(config, migration)
    db = database.connect(config.state_root)
    other = auth.create_client(db, "other-preflight-owner", False)
    for vid in (migration["source"], migration["target"]):
        auth.set_grant(db, other["id"], vid, query=True, original=False, add=True)
    db.close()
    with pytest.raises(StorageError):
        note_migration.run(
            config,
            "Bearer " + other["token"],
            migration["source"],
            migration["target"],
            "check",
            plan_id=planned["plan_id"],
        )
    with pytest.raises(StorageError):
        note_migration.run(
            config,
            "Bearer " + migration["client"]["token"],
            migration["target"],
            migration["source"],
            "check",
            plan_id=planned["plan_id"],
        )


def test_capacity_and_parallel_duplicates_are_bounded(config, migration):
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: run(config, migration), range(2)))
    assert first == second
    with patch.object(note_migration_plans, "MAX_PLANS", 1):
        assert run(config, migration) == first
        (migration["root"] / "manual.md").write_text("New explicit body")
        with pytest.raises(StorageError):
            run(config, migration)
    assert len(list(path(config, migration, first).parent.iterdir())) == 1


def test_cli_returns_explicit_nonexecution_and_conflict_exit_code(config, migration):
    token = config.project_root / "test-token.txt"
    token.write_text(migration["client"]["token"])
    base = [sys.executable, "-m", "fiber_bundle.note_migration_cli"]
    common = [
        "--project-root",
        str(config.project_root),
        "--config",
        "config.json",
        "--token-file",
        str(token),
        "--source-vault-id",
        migration["source"],
        "--target-vault-id",
        migration["target"],
    ]
    env = {**os.environ, "PYTHONPATH": str(Path(note_migration.__file__).parents[1])}

    def cli(action, *args):
        return subprocess.run(
            base + [action] + common + list(args),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    result = cli(
        "plan",
        "--note-identity",
        migration["identity"],
        "--revision",
        "1",
        "--source-path",
        "manual.md",
        "--target-path",
        "迁移/历史笔记.md",
        "--expected-sha256",
        sha(migration["root"] / "manual.md"),
    )
    assert result.returncode == 0
    value = json.loads(result.stdout)
    assert value["execution_available"] is False
    assert cli("check", "--plan-id", value["plan_id"]).returncode == 0
    (migration["root"] / "manual.md").write_text("Current human edit")
    assert cli("check", "--plan-id", value["plan_id"]).returncode == 2
    failure = cli("check", "--plan-id", "invalid-sensitive-input")
    assert failure.returncode == 1 and "invalid-sensitive-input" not in failure.stdout
    assert json.loads(failure.stdout)["error_code"] == "note_migration_preflight_rejected"
    assert cli("apply", "--plan-id", value["plan_id"]).returncode == 1


def test_real_process_exit_after_plan_save_leaves_only_nonexecutable_metadata(config, migration):
    token = config.project_root / "crash-token.txt"
    token.write_text(migration["client"]["token"])
    script = """
import os,sys
from pathlib import Path
from fiber_bundle import note_migration,note_migration_plans
from fiber_bundle.config import load_config
root=Path(sys.argv[1]);c=load_config(root/'config.json',root);save=note_migration_plans.save
def crash(*a,**k):save(*a,**k);os._exit(94)
note_migration_plans.save=crash
note_migration.run(c,'Bearer '+(root/'crash-token.txt').read_text(),sys.argv[2],sys.argv[3],
 'plan',note_identity=sys.argv[4],revision=1,source_path='manual.md',target_path='迁移/历史笔记.md',expected_sha256=sys.argv[5])
"""
    before = files(migration["root"]), files(migration["destination"])
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(config.project_root),
            migration["source"],
            migration["target"],
            migration["identity"],
            sha(migration["root"] / "manual.md"),
        ],
        capture_output=True,
        timeout=20,
        env={**os.environ, "PYTHONPATH": str(Path(note_migration.__file__).parents[1])},
    )
    assert result.returncode == 94
    planned = run(config, migration)
    assert run(config, migration, "check", plan_id=planned["plan_id"])["status"] == "match"
    assert (files(migration["root"]), files(migration["destination"])) == before


def test_published_article_is_refused_before_freezing_any_migration(
    config, article_scope, published
):
    db, result, note, _ = published
    client, source, _ = article_scope
    token = "synthetic-publication-migration-token"
    db.execute("UPDATE clients SET token_digest=? WHERE id=?", (auth.digest(token), client))
    target = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
    auth.set_grant(db, client, target, query=True, original=False, add=True)
    with pytest.raises(StorageError):
        note_migration.run(
            config,
            "Bearer " + token,
            source,
            target,
            "plan",
            note_identity="publication:" + result["publication_id"],
            revision=1,
            source_path=result["note_path"],
            target_path="copied.md",
            expected_sha256=sha(note),
        )
    assert not (config.state_root / "note-migrations").exists()
    assert note.exists()


def test_target_moc_and_metadata_changes_invalidate_fixed_plan(config, migration):
    planned = run(config, migration)
    token = "Bearer " + migration["client"]["token"]
    mid = str(uuid4())
    moc_associations.run(
        config, token, migration["target"], "create", mid, title="Target review MOC", revision=0
    )
    result = run(config, migration, "check", plan_id=planned["plan_id"])
    assert result["status"] == "conflict"
    assert result["changed_fields"] == ["target_mocs_sha256"]


def test_failure_to_sync_plan_preserves_bodies_and_does_not_create_an_execution_claim(
    config, migration
):
    before = files(migration["root"]), files(migration["destination"])
    sync = note_migration_plans.sync_directory

    def fail(path):
        if path.name == migration["target"]:
            raise OSError("sync failure")
        return sync(path)

    with (
        patch.object(note_migration_plans, "sync_directory", side_effect=fail),
        pytest.raises(OSError),
    ):
        run(config, migration)
    planned = run(config, migration)
    assert run(config, migration, "check", plan_id=planned["plan_id"])["status"] == "match"
    assert (files(migration["root"]), files(migration["destination"])) == before
    assert list(path(config, migration, planned).parent.iterdir()) == [
        path(config, migration, planned)
    ]
