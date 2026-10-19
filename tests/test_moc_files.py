import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from test_moc_associations import link
from test_moc_associations import moc_scope as moc_scope
from test_moc_associations import run as metadata
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope

from fiber_bundle import auth, database, file_exchange, moc_file_journal, moc_files, moc_markdown
from fiber_bundle.config import InvalidInput, StorageError


@pytest.fixture
def file_scope(config, moc_scope):
    metadata(config, moc_scope)
    link(config, moc_scope)
    (moc_scope[2] / "00-Maps").mkdir()
    (moc_scope[2] / "00-Maps/topic MOC.md").write_bytes(
        b"---\r\ntype: moc\r\nupdated: human-date\r\n---\r\n# Human MOC\r\n"
        b"\r\n## Human\r\n[[other]]\r\n"
    )
    return moc_scope


def invoke(config, scope, action, **kwargs):
    return moc_files.run(
        config, "Bearer " + scope[0]["token"], scope[1], action, scope[3], **kwargs
    )


def plan(config, scope, relative="00-Maps/topic MOC.md", revision=2):
    path = scope[2] / relative
    expected = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"
    return invoke(
        config, scope, "plan-file", revision=revision, relative=relative, expected_sha256=expected
    )


def apply(config, scope, planned):
    return invoke(config, scope, "apply-file", plan_id=planned["plan_id"])


def test_plan_then_exchange_preserves_all_human_bytes_and_original_inode(config, file_scope):
    target = file_scope[2] / "00-Maps/topic MOC.md"
    note = file_scope[2] / "manual.md"
    original, inode = target.read_bytes(), target.stat().st_ino
    note_before = note.read_bytes(), note.stat().st_mtime_ns, note.stat().st_ino
    planned = plan(config, file_scope)
    assert target.read_bytes() == original and planned["moc_file_write_performed"] is False
    assert "../manual.md" in planned["markdown"] and "updated: human-date" in planned["markdown"]
    result = apply(config, file_scope, planned)
    assert result["moc_file_write_performed"] is True
    assert target.read_bytes().startswith(original)
    assert Path(result["backup_path"]).read_bytes() == original
    assert Path(result["backup_path"]).stat().st_ino == inode
    assert (note.read_bytes(), note.stat().st_mtime_ns, note.stat().st_ino) == note_before
    assert apply(config, file_scope, planned)["moc_file_write_performed"] is False
    assert plan(config, file_scope)["status"] == "unchanged"


def test_new_moc_created_exclusively_and_nested_relative_links(config, file_scope):
    planned = plan(config, file_scope, "00-Maps/new MOC.md")
    target = file_scope[2] / "00-Maps/new MOC.md"
    assert not target.exists() and planned["before_sha256"] == "absent"
    result = apply(config, file_scope, planned)
    assert result["backup_path"] is None and target.read_bytes().startswith(
        b"---\ntype: moc\n---\n"
    )
    assert "../manual.md" in target.read_text()
    assert not Path(
        config.state_root
        / f"moc-files/{file_scope[1]}/{file_scope[3]}/{planned['plan_id']}/candidate.md"
    ).exists()


def test_existing_wikilinks_and_yaml_stay_human_owned(config, file_scope):
    target = file_scope[2] / "00-Maps/topic MOC.md"
    original = b"---\ntype: moc\ntags: [human]\n---\n# Existing\n[[manual|Human alias]]\n"
    target.write_bytes(original)
    planned = plan(config, file_scope)
    assert planned["manual_links_preserved"] == 1
    assert "../manual.md" not in planned["markdown"]
    apply(config, file_scope, planned)
    metadata(config, file_scope, "unlink-system", revision=2, note_identity=file_scope[4])
    planned = plan(config, file_scope, revision=3)
    if planned["status"] != "unchanged":
        apply(config, file_scope, planned)
    assert (
        target.read_bytes().startswith(original)
        and b"[[manual|Human alias]]" in target.read_bytes()
    )


def test_managed_update_preserves_added_human_suffix_and_moves_link(config, file_scope):
    target = file_scope[2] / "00-Maps/topic MOC.md"
    original = target.read_bytes()
    apply(config, file_scope, plan(config, file_scope))
    target.write_bytes(target.read_bytes() + b"\r\n## Added human section\r\nkeep this\r\n")
    (file_scope[2] / "manual.md").rename(file_scope[2] / "renamed.md")
    planned = plan(config, file_scope)
    assert "../renamed.md" in planned["markdown"] and "../manual.md" not in planned["markdown"]
    apply(config, file_scope, planned)
    assert target.read_bytes().startswith(original)
    assert target.read_bytes().endswith(b"\r\n## Added human section\r\nkeep this\r\n")


@pytest.mark.parametrize("edit", ["managed", "delete-block", "delete-file"])
def test_user_edits_or_deletion_are_not_overwritten_or_revived(config, file_scope, edit):
    target = file_scope[2] / "00-Maps/topic MOC.md"
    apply(config, file_scope, plan(config, file_scope))
    if edit == "managed":
        target.write_bytes(target.read_bytes().replace(b"manual.md", b"human.md"))
    elif edit == "delete-block":
        target.write_text("# Human replacement\n")
    else:
        target.unlink()
    before = target.read_bytes() if target.exists() else None
    with pytest.raises(StorageError):
        plan(config, file_scope)
    assert (target.read_bytes() if target.exists() else None) == before


@pytest.mark.parametrize("change", ["target", "source", "revision", "candidate"])
def test_stale_plan_rejects_without_claim_or_write(config, file_scope, change):
    planned = plan(config, file_scope)
    target = file_scope[2] / "00-Maps/topic MOC.md"
    if change == "target":
        target.write_text("human newest content")
    elif change == "source":
        (file_scope[2] / "manual.md").write_text("new source")
    elif change == "revision":
        metadata(config, file_scope, "unlink-system", revision=2, note_identity=file_scope[4])
    else:
        p = moc_file_journal.plans(config, file_scope[1], file_scope[3])[0]
        (p.path / "candidate.md").write_text("tamper")
    before = target.read_bytes()
    with pytest.raises(StorageError):
        apply(config, file_scope, planned)
    assert target.read_bytes() == before
    assert not any(
        p.status == "pending" for p in moc_file_journal.plans(config, file_scope[1], file_scope[3])
    )


def test_new_target_race_no_replace_and_explicit_reconcile_does_not_replay(config, file_scope):
    planned = plan(config, file_scope, "00-Maps/new.md")
    target = file_scope[2] / "00-Maps/new.md"
    rename = file_exchange.rename

    def raced(*args, **kwargs):
        target.write_text("external new file")
        return rename(*args, **kwargs)

    with patch.object(file_exchange, "rename", side_effect=raced), pytest.raises(OSError):
        apply(config, file_scope, planned)
    assert target.read_text() == "external new file"
    with pytest.raises(StorageError):
        apply(config, file_scope, planned)
    with pytest.raises(StorageError):
        invoke(config, file_scope, "reconcile-file", plan_id=planned["plan_id"])
    assert target.read_text() == "external new file"


@pytest.mark.parametrize("same_bytes", [False, True])
def test_race_at_exchange_preserves_external_replacement(config, file_scope, same_bytes):
    planned = plan(config, file_scope)
    target = file_scope[2] / "00-Maps/topic MOC.md"
    external = target.read_bytes() if same_bytes else b"# External replacement\n"
    rename = file_exchange.rename
    calls = 0
    new_key = None

    def raced(*args, **kwargs):
        nonlocal calls, new_key
        calls += 1
        if calls == 1:
            replacement = target.with_name("external.md")
            replacement.write_bytes(external)
            replacement.replace(target)
            new_key = target.stat().st_ino
        return rename(*args, **kwargs)

    with patch.object(file_exchange, "rename", side_effect=raced), pytest.raises(StorageError):
        apply(config, file_scope, planned)
    assert target.read_bytes() == external and target.stat().st_ino == new_key
    assert moc_file_journal.plans(config, file_scope[1], file_scope[3])[0].status == "not-applied"


@pytest.mark.parametrize("after_swap", [False, True])
def test_pending_claim_requires_explicit_reconcile_no_reexecution(config, file_scope, after_swap):
    planned = plan(config, file_scope)
    target = file_scope[2] / "00-Maps/topic MOC.md"
    original = target.read_bytes()
    if after_swap:
        failing = patch.object(moc_file_journal, "finish", side_effect=OSError("disk failure"))
    else:
        failing = patch.object(file_exchange, "rename", side_effect=OSError("pre-syscall failure"))
    with failing, pytest.raises(OSError):
        apply(config, file_scope, planned)
    assert (target.read_bytes() != original) is after_swap
    with pytest.raises(StorageError):
        apply(config, file_scope, planned)
    with pytest.raises(StorageError):
        plan(config, file_scope)
    before = target.read_bytes()
    result = invoke(config, file_scope, "reconcile-file", plan_id=planned["plan_id"])
    assert result["status"] == ("applied" if after_swap else "not-applied")
    assert result["moc_file_write_performed"] is False and target.read_bytes() == before


def test_revoke_after_exchange_rolls_back_only_own_projection(config, file_scope):
    planned = plan(config, file_scope)
    target = file_scope[2] / "00-Maps/topic MOC.md"
    original, inode = target.read_bytes(), target.stat().st_ino
    rename = file_exchange.rename
    calls = 0

    def revoked(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = rename(*args, **kwargs)
        if calls == 1:
            db = database.connect(config.state_root)
            auth.set_grant(
                db, file_scope[0]["id"], file_scope[1], query=False, original=False, add=True
            )
            db.close()
        return result

    with patch.object(file_exchange, "rename", side_effect=revoked), pytest.raises(auth.Denied):
        apply(config, file_scope, planned)
    assert (target.read_bytes(), target.stat().st_ino) == (original, inode)


@pytest.mark.parametrize("query,add", [(False, False), (True, False), (False, True)])
def test_file_plan_requires_existing_query_and_add(config, file_scope, query, add):
    db = database.connect(config.state_root)
    auth.set_grant(db, file_scope[0]["id"], file_scope[1], query=query, original=False, add=add)
    db.close()
    with pytest.raises(auth.Denied):
        plan(config, file_scope)
    assert not (config.state_root / "moc-files").exists()


@pytest.mark.parametrize(
    "path", ["../outside.md", "/abs.md", ".hidden/moc.md", "00-Maps/../moc.md", "moc.txt"]
)
def test_invalid_file_paths_rejected(config, file_scope, path):
    with pytest.raises(StorageError):
        invoke(config, file_scope, "plan-file", revision=2, relative=path, expected_sha256="absent")
    assert not (config.state_root / "moc-files").exists()


@pytest.mark.parametrize("danger", ["symlink", "hardlink", "fifo", "marker"])
def test_dangerous_or_owned_targets_rejected(config, file_scope, danger):
    target = file_scope[2] / "00-Maps/topic MOC.md"
    original = target.read_bytes()
    if danger == "marker":
        (target.parent / ".fiber-publication.json").write_text("{}")
    else:
        target.unlink()
        if danger == "symlink":
            target.symlink_to(file_scope[2] / "manual.md")
        elif danger == "hardlink":
            os.link(file_scope[2] / "manual.md", target)
        else:
            os.mkfifo(target)
    with pytest.raises((StorageError, OSError)):
        invoke(
            config,
            file_scope,
            "plan-file",
            revision=2,
            relative="00-Maps/topic MOC.md",
            expected_sha256=hashlib.sha256(original).hexdigest(),
        )
    assert not (config.state_root / "moc-files").exists()


def test_conflicting_markers_and_manual_duplicate_names_are_conservative(config, file_scope):
    target = file_scope[2] / "00-Maps/topic MOC.md"
    target.write_text("[[manual]]\n")
    (file_scope[2] / "folder").mkdir()
    (file_scope[2] / "folder/manual.md").write_text("copy with duplicate basename")
    planned = plan(config, file_scope)
    assert planned["manual_links_preserved"] == 0 and "../manual.md" in planned["markdown"]
    target.write_bytes(moc_markdown.markers(file_scope[3])[0] + b"\ncorrupt")
    with pytest.raises(StorageError):
        plan(config, file_scope)


def test_stale_second_plan_and_tampered_plan_rejected(config, file_scope):
    first, second = plan(config, file_scope), plan(config, file_scope)
    apply(config, file_scope, first)
    with pytest.raises(StorageError):
        apply(config, file_scope, second)
    p = next(
        p
        for p in moc_file_journal.plans(config, file_scope[1], file_scope[3])
        if p.plan_id == second["plan_id"]
    )
    (p.path / "plan.json").write_text("{}")
    with pytest.raises(StorageError):
        plan(config, file_scope)


def test_write_only_policy_does_not_replace_application_write_contract(config, file_scope):
    (file_scope[2] / ".knowledge-organizer-policy.json").write_text(
        '{"schema_version":1,"deny_paths":[{"path":"00-Maps","deny":["write"]}]}'
    )
    apply(config, file_scope, plan(config, file_scope))
    assert b"fiber-moc:v1" in (file_scope[2] / "00-Maps/topic MOC.md").read_bytes()


def test_real_cli_plan_apply_reconcile_argument_errors(config, file_scope):
    target = file_scope[2] / "00-Maps/topic MOC.md"
    token = config.project_root / "token.txt"
    token.write_text(file_scope[0]["token"])
    common = [
        sys.executable,
        "-m",
        "fiber_bundle.moc_cli",
        "--project-root",
        str(config.project_root),
        "--config",
        "config.json",
        "--token-file",
        str(token),
        "--vault-id",
        file_scope[1],
        "--moc-id",
        file_scope[3],
    ]
    env = {**os.environ, "PYTHONPATH": str(Path(moc_files.__file__).parents[1])}

    def cli(*args):
        return subprocess.run(
            common + list(args), env=env, text=True, capture_output=True, timeout=20
        )

    result = cli(
        "plan-file",
        "--revision",
        "2",
        "--moc-path",
        "00-Maps/topic MOC.md",
        "--expected-sha256",
        hashlib.sha256(target.read_bytes()).hexdigest(),
    )
    assert result.returncode == 0
    pid = json.loads(result.stdout)["plan_id"]
    assert cli("apply-file", "--plan-id", pid).returncode == 0
    bad = cli("apply-file", "--plan-id", pid, "--owner", "manual")
    assert bad.returncode == 1 and file_scope[0]["token"] not in bad.stdout
    assert cli("reconcile-file", "--plan-id", pid).returncode == 0


def test_fenced_inline_code_and_comments_are_not_manual_navigation(config, file_scope):
    target = file_scope[2] / "00-Maps/topic MOC.md"
    target.write_text("```md\n[[manual]]\n```\n`[[manual]]`\n<!-- [[manual]] -->\n")
    planned = plan(config, file_scope)
    assert planned["manual_links_preserved"] == 0 and "../manual.md" in planned["markdown"]


def test_file_permissions_and_empty_existing_moc_preserved(config, file_scope):
    target = file_scope[2] / "00-Maps/topic MOC.md"
    target.write_bytes(b"")
    target.chmod(0o640)
    planned = plan(config, file_scope)
    result = apply(config, file_scope, planned)
    assert target.stat().st_mode & 0o777 == 0o640
    assert Path(result["backup_path"]).read_bytes() == b""


def test_real_process_exit_after_exchange_is_reconciled_without_replay(config, file_scope):
    planned = plan(config, file_scope)
    target = file_scope[2] / "00-Maps/topic MOC.md"
    original = target.read_bytes()
    token = config.project_root / "crash-token.txt"
    token.write_text(file_scope[0]["token"])
    script = """
import os,sys
from pathlib import Path
from fiber_bundle import file_exchange,moc_files
from fiber_bundle.config import load_config
root=Path(sys.argv[1]);config=load_config(root/'config.json',root)
rename=file_exchange.rename
def crash(*args,**kwargs):
    rename(*args,**kwargs)
    os._exit(91)
file_exchange.rename=crash
moc_files.run(config,'Bearer '+(root/'crash-token.txt').read_text(),sys.argv[2],
              'apply-file',sys.argv[3],plan_id=sys.argv[4])
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(config.project_root),
            file_scope[1],
            file_scope[3],
            planned["plan_id"],
        ],
        timeout=20,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(moc_files.__file__).parents[1])},
    )
    assert result.returncode == 91 and target.read_bytes().startswith(original)
    assert moc_file_journal.plans(config, file_scope[1], file_scope[3])[0].status == "pending"
    current = target.read_bytes()
    result = invoke(config, file_scope, "reconcile-file", plan_id=planned["plan_id"])
    assert result["status"] == "applied" and target.read_bytes() == current


def test_root_replacement_after_exchange_preserves_original_in_old_root(config, file_scope):
    planned = plan(config, file_scope)
    root = file_scope[2]
    original = (root / "00-Maps/topic MOC.md").read_bytes()
    rename = file_exchange.rename
    calls = 0
    old_root = root.with_name("old-root")

    def moved(*args, **kwargs):
        nonlocal calls
        result = rename(*args, **kwargs)
        calls += 1
        if calls == 1:
            root.rename(old_root)
            root.mkdir()
        return result

    with patch.object(file_exchange, "rename", side_effect=moved), pytest.raises(StorageError):
        apply(config, file_scope, planned)
    assert (old_root / "00-Maps/topic MOC.md").read_bytes() == original
    assert not list(root.iterdir())


def test_write_plan_capacity_refuses_additional_plan(config, file_scope):
    first = plan(config, file_scope)
    with patch.object(moc_file_journal, "MAX_PLANS", 1), pytest.raises(StorageError):
        plan(config, file_scope)
    assert (
        moc_file_journal.plans(config, file_scope[1], file_scope[3])[0].plan_id == first["plan_id"]
    )


def test_excluded_moc_target_is_denied_before_file_access(config, file_scope, monkeypatch):
    from fiber_bundle import database

    db = database.connect(config.state_root)
    db.execute(
        "UPDATE vaults SET scope_json=? WHERE id=?",
        ('{"include":[""],"exclude":["00-Maps"]}', file_scope[1]),
    )
    db.close()
    monkeypatch.setattr(moc_files, "target", lambda *args: pytest.fail("excluded target read"))
    with pytest.raises(InvalidInput, match="scope_denied"):
        plan(config, file_scope)
