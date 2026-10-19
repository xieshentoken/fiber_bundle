import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
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

from fiber_bundle import auth, database, moc_associations, moc_store, search
from fiber_bundle.config import StorageError
from fiber_bundle.inputs import canonical


@pytest.fixture
def moc_scope(config, identity_scope):
    registered = track(config, identity_scope)
    return (*identity_scope, str(uuid4()), registered["note_identity"])


def run(config, scope, action="create", **kwargs):
    client, vid, _, mid, _ = scope
    if action == "create":
        kwargs = {"title": "合成 MOC", "revision": 0, **kwargs}
    return moc_associations.run(config, "Bearer " + client["token"], vid, action, mid, **kwargs)


def link(config, scope, revision=1, owner="system", **kwargs):
    return run(
        config,
        scope,
        "link",
        revision=revision,
        owner=owner,
        note_identity=scope[4],
        expected_sha256=hashlib.sha256((scope[2] / "manual.md").read_bytes()).hexdigest(),
        **kwargs,
    )


def ledger(config):
    with moc_store.connection(config) as db:
        assert db is not None
        return (
            [row[:] for row in db.execute("SELECT * FROM mocs ORDER BY id")],
            [row[:] for row in db.execute("SELECT * FROM events ORDER BY id")],
        )


def test_metadata_only_preview_idempotence_and_search_independence(config, moc_scope):
    root = moc_scope[2]
    (root / "Old MOC.md").write_bytes(b"---\nmoc: human\n---\n# Human\n[[manual]]\r\n")
    before = {
        p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in root.iterdir()
    }
    result = run(config, moc_scope)
    assert result["revision"] == 1 and result["members"] == []
    assert run(config, moc_scope)["metadata_changed"] is False
    result = link(config, moc_scope)
    assert result["revision"] == 2 and result["metadata_changed"] is True
    assert result["members"][0]["owners"] == ["system"]
    assert "[manual.md](<manual.md>)" in result["markdown"]
    assert link(config, moc_scope, revision=2)["metadata_changed"] is False
    assert len(ledger(config)[1]) == 2
    assert run(config, moc_scope, "show")["moc_file_write_performed"] is False
    assert before == {
        p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in root.iterdir()
    }
    path = config.state_root / "moc-associations.sqlite3"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert b"private-body-sentinel" not in path.read_bytes()
    assert moc_scope[0]["token"].encode() not in path.read_bytes()
    db = database.connect(config.state_root)
    result = search.search(config, db, moc_scope[0]["id"], "sentinel", [moc_scope[1]])
    assert result["results"][0]["note_id"] == moc_scope[4]
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    db.close()


@pytest.mark.parametrize("first", ["manual", "system"])
def test_manual_ownership_survives_system_link_removal(config, moc_scope, first):
    run(config, moc_scope)
    link(config, moc_scope, owner=first)
    link(config, moc_scope, revision=2, owner="manual" if first == "system" else "system")
    assert run(config, moc_scope, "show")["members"][0]["owners"] == ["manual", "system"]
    result = run(config, moc_scope, "unlink-system", revision=3, note_identity=moc_scope[4])
    assert result["revision"] == 4 and result["members"][0]["owners"] == ["manual"]
    assert (
        run(config, moc_scope, "unlink-system", revision=4, note_identity=moc_scope[4])[
            "metadata_changed"
        ]
        is False
    )
    with moc_store.connection(config) as db:
        events = db.execute("SELECT record_json FROM events ORDER BY id").fetchall()
    assert json.loads(events[2][0])["members"][moc_scope[4]] == ["manual", "system"]
    assert json.loads(events[3][0])["members"][moc_scope[4]] == ["manual"]


def test_remove_system_only_and_missing_members_preserve_human(config, moc_scope):
    run(config, moc_scope)
    link(config, moc_scope)
    result = run(config, moc_scope, "unlink-system", revision=2, note_identity=moc_scope[4])
    assert result["members"] == [] and result["revision"] == 3
    link(config, moc_scope, revision=3, owner="manual")
    (moc_scope[2] / "manual.md").unlink()
    before = ledger(config)
    result = run(config, moc_scope, "show")
    assert result["members"][0]["status"] == "missing"
    assert result["members"][0]["note_path"] is None and "归属保留" in result["markdown"]
    assert result["members"][0]["owners"] == ["manual"]
    assert ledger(config) == before and not (moc_scope[2] / "manual.md").exists()


def test_move_edit_copy_and_url_escaping_keep_identity(config, moc_scope):
    run(config, moc_scope)
    link(config, moc_scope, owner="manual")
    root = moc_scope[2]
    original = root / "manual.md"
    (root / "copy.md").write_bytes(original.read_bytes())
    target = root / "folder [x]"
    target.mkdir()
    original.rename(target / "name](bad)#中文.md")
    (target / "name](bad)#中文.md").write_text("human changed-sentinel\n")
    before = ledger(config)
    result = run(config, moc_scope, "show")
    assert result["members"][0]["note_path"] == "folder [x]/name](bad)#中文.md"
    assert len(result["members"]) == 1 and "copy.md" not in result["markdown"]
    assert "folder%20%5Bx%5D/name%5D%28bad%29%23" in result["markdown"]
    assert ledger(config) == before


def test_published_note_move_uses_source_identity(config, article_scope, published):
    db, result, note, _ = published
    cid, vid, _ = article_scope
    client = auth.create_client(db, "MOC publisher", False)
    auth.set_grant(db, client["id"], vid, query=True, original=False, add=True)
    db.close()
    local = (client, vid, note.parents[1], str(uuid4()), "publication:" + result["publication_id"])
    run(config, local)
    run(
        config,
        local,
        "link",
        revision=1,
        owner="system",
        note_identity=local[4],
        expected_sha256=hashlib.sha256(note.read_bytes()).hexdigest(),
    )
    moved = note.parent.parent / "moved-publication"
    note.parent.rename(moved)
    preview = run(config, local, "show")
    assert preview["members"][0]["note_identity"] == local[4]
    assert preview["members"][0]["note_path"] == "moved-publication/note.md"


def test_stale_revision_wrong_hash_and_unknown_identity_do_not_mutate(config, moc_scope):
    run(config, moc_scope)
    before = ledger(config)
    with pytest.raises(StorageError):
        run(
            config,
            moc_scope,
            "link",
            revision=1,
            owner="system",
            note_identity=moc_scope[4],
            expected_sha256="0" * 64,
        )
    with pytest.raises(StorageError):
        run(
            config,
            moc_scope,
            "link",
            revision=1,
            owner="manual",
            note_identity="note:" + str(uuid4()),
            expected_sha256="0" * 64,
        )
    assert ledger(config) == before
    link(config, moc_scope)
    before = ledger(config)
    with pytest.raises(StorageError):
        link(config, moc_scope, revision=1, owner="manual")
    with pytest.raises(StorageError):
        run(config, moc_scope, title="different")
    with pytest.raises(StorageError):
        run(config, moc_scope)  # creation retry cannot overwrite a populated MOC
    assert ledger(config) == before


@pytest.mark.parametrize("query,add", [(False, False), (True, False), (False, True)])
def test_mutation_requires_query_and_add_before_sidecar_creation(config, moc_scope, query, add):
    client, vid, *_ = moc_scope
    db = database.connect(config.state_root)
    auth.set_grant(db, client["id"], vid, query=query, original=False, add=add)
    db.close()
    with pytest.raises(auth.Denied):
        run(config, moc_scope)
    assert not (config.state_root / "moc-associations.sqlite3").exists()


def test_query_peer_shares_preview_but_cannot_mutate_and_cross_vault_rejected(config, moc_scope):
    run(config, moc_scope)
    link(config, moc_scope, owner="manual")
    db = database.connect(config.state_root)
    peer = auth.create_client(db, "MOC reader", False)
    vid = moc_scope[1]
    other = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
    auth.set_grant(db, peer["id"], vid, query=True, original=False, add=False)
    auth.set_grant(db, peer["id"], other, query=True, original=False, add=True)
    db.close()
    shared = (peer, *moc_scope[1:])
    before = ledger(config)
    assert run(config, shared, "show")["members"][0]["owners"] == ["manual"]
    with pytest.raises(auth.Denied):
        run(config, shared, "unlink-system", revision=2, note_identity=moc_scope[4])
    second = (peer, other, moc_scope[2], moc_scope[3], moc_scope[4])
    with pytest.raises(StorageError):
        run(config, second, "show")
    assert ledger(config) == before
    run(config, second)
    with pytest.raises(StorageError):
        link(config, second)


@pytest.mark.parametrize("action", ["show", "link"])
def test_change_during_observation_rejects_preview_or_commit(config, moc_scope, action):
    run(config, moc_scope)
    before = ledger(config)
    read = moc_associations.checked_inventory
    calls = 0

    def changed(*args):
        nonlocal calls
        result = read(*args)
        calls += 1
        if calls == 1:
            (moc_scope[2] / "manual.md").write_text("external edit")
        return result

    with (
        patch.object(moc_associations, "checked_inventory", side_effect=changed),
        pytest.raises(StorageError),
    ):
        link(config, moc_scope) if action == "link" else run(config, moc_scope, "show")
    assert ledger(config) == before


@pytest.mark.parametrize("grant", ["query", "add"])
def test_revoke_before_commit_keeps_ledger_absent(config, moc_scope, grant):
    read = moc_associations.checked_inventory
    calls = 0

    def revoked(*args):
        nonlocal calls
        result = read(*args)
        calls += 1
        if calls == 2:
            db = args[1]
            auth.set_grant(
                db,
                moc_scope[0]["id"],
                moc_scope[1],
                query=grant != "query",
                original=False,
                add=grant != "add",
            )
        return result

    with (
        patch.object(moc_associations, "checked_inventory", side_effect=revoked),
        pytest.raises(auth.Denied),
    ):
        run(config, moc_scope)
    assert not (config.state_root / "moc-associations.sqlite3").exists()


def test_root_replacement_and_note_replacement_reject(config, moc_scope):
    run(config, moc_scope)
    link(config, moc_scope)
    before = ledger(config)
    root = moc_scope[2]
    tmp = root / "replacement.md"
    tmp.write_bytes((root / "manual.md").read_bytes())
    tmp.replace(root / "manual.md")
    with pytest.raises(StorageError):
        run(config, moc_scope, "show")
    old = root.with_name("old-vault")
    root.rename(old)
    root.mkdir()
    shutil.copy2(old / "manual.md", root / "manual.md")
    with pytest.raises(StorageError):
        run(config, moc_scope, "show")
    assert ledger(config) == before


@pytest.mark.parametrize(
    "danger",
    ["symlink", "hardlink", "fifo", "schema", "journal", "journal-hardlink", "journal-fifo"],
)
def test_dangerous_ledger_rejected_without_note_changes(config, moc_scope, danger):
    path = config.state_root / "moc-associations.sqlite3"
    target = config.state_root / "outside"
    target.write_text("private sentinel")
    if danger == "symlink":
        path.symlink_to(target)
    elif danger == "hardlink":
        os.link(target, path)
    elif danger == "fifo":
        os.mkfifo(path)
    elif danger == "journal":
        path.with_name(path.name + "-journal").symlink_to(target)
    elif danger == "journal-hardlink":
        os.link(target, path.with_name(path.name + "-journal"))
    elif danger == "journal-fifo":
        os.mkfifo(path.with_name(path.name + "-journal"))
    else:
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA user_version=9")
    before = target.read_bytes()
    with pytest.raises((StorageError, sqlite3.Error)):
        run(config, moc_scope)
    assert target.read_bytes() == before


@pytest.mark.parametrize("bad", ["owner", "fields", "revision", "duplicate", "untracked"])
def test_noncanonical_or_invalid_ledger_record_is_rejected(config, moc_scope, bad):
    run(config, moc_scope)
    with moc_store.connection(config, write=True) as db:
        raw = db.execute("SELECT record_json FROM mocs").fetchone()[0]
        value = json.loads(raw)
        if bad == "owner":
            value["members"] = {moc_scope[4]: ["automatic"]}
        elif bad == "fields":
            value["body"] = "not allowed"
        elif bad == "revision":
            value["revision"] = True
        elif bad == "untracked":
            value["members"] = {"path:manual.md": ["manual"]}
        else:
            raw = raw[:-1] + ',"title":"合成 MOC"}'
        db.execute(
            "UPDATE mocs SET record_json=?", (raw if bad == "duplicate" else canonical(value),)
        )
    with pytest.raises(StorageError):
        run(config, moc_scope, "show")


def test_limits_and_audit_failure_are_transactional(config, moc_scope):
    with patch.object(moc_store, "MAX_MOCS", 0), pytest.raises(StorageError):
        run(config, moc_scope)
    assert ledger(config) == ([], [])
    run(config, moc_scope)
    before = ledger(config)
    with patch.object(moc_store, "MAX_MEMBERS", 0), pytest.raises(StorageError):
        link(config, moc_scope)
    assert ledger(config) == before
    with moc_store.connection(config, write=True) as db:
        db.execute(
            "CREATE TRIGGER deny_event BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT,'stop'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        link(config, moc_scope)
    assert ledger(config) == before


def test_concurrent_revision_only_one_writer_succeeds(config, moc_scope):
    run(config, moc_scope)

    def attempt(owner):
        try:
            return link(config, moc_scope, owner=owner)["revision"]
        except StorageError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ["manual", "system"]))
    assert sorted(map(str, outcomes)) == ["2", "conflict"]
    assert len(ledger(config)[1]) == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": "\nInjected"},
        {"title": " "},
        {"title": "a" * 201},
        {"revision": True},
        {"revision": 1},
        {"note_identity": "path:manual.md"},
        {"owner": "manual"},
    ],
)
def test_invalid_create_arguments_do_not_create_ledger(config, moc_scope, kwargs):
    with pytest.raises(StorageError):
        run(config, moc_scope, **kwargs)
    assert not (config.state_root / "moc-associations.sqlite3").exists()


def test_missing_show_is_read_only_and_unclassified_notes_searchable(config, moc_scope):
    with pytest.raises(StorageError):
        run(config, moc_scope, "show")
    assert not (config.state_root / "moc-associations.sqlite3").exists()
    db = database.connect(config.state_root)
    assert search.search(config, db, moc_scope[0]["id"], "sentinel", [moc_scope[1]])["results"]
    db.close()


def test_title_markdown_escaping(config, moc_scope):
    result = run(config, moc_scope, title="[title](file) <script> *x*")
    assert "&lt;script&gt;" in result["markdown"]
    assert "\\[title\\]\\(file\\)" in result["markdown"]


def test_actual_cli_roundtrip_and_safe_errors(config, moc_scope):
    token = config.project_root / "token.txt"
    token.write_text(moc_scope[0]["token"])
    base = [sys.executable, "-m", "fiber_bundle.moc_cli"]
    common = [
        "--project-root",
        str(config.project_root),
        "--config",
        "config.json",
        "--token-file",
        str(token),
        "--vault-id",
        moc_scope[1],
        "--moc-id",
        moc_scope[3],
    ]
    env = {**os.environ, "PYTHONPATH": str(Path(moc_associations.__file__).parents[1])}

    def cli(action, *args):
        return subprocess.run(
            base + [action] + common + list(args),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    created = cli("create", "--title", "CLI MOC", "--revision", "0")
    assert created.returncode == 0 and json.loads(created.stdout)["revision"] == 1
    linked = cli(
        "link",
        "--revision",
        "1",
        "--owner",
        "manual",
        "--note-identity",
        moc_scope[4],
        "--expected-sha256",
        hashlib.sha256((moc_scope[2] / "manual.md").read_bytes()).hexdigest(),
    )
    assert linked.returncode == 0 and json.loads(linked.stdout)["revision"] == 2
    shown = cli("show")
    assert shown.returncode == 0 and json.loads(shown.stdout)["members"][0]["owners"] == ["manual"]
    bad = cli("unlink-system", "--revision", "1", "--note-identity", moc_scope[4])
    assert bad.returncode == 1 and json.loads(bad.stdout) == {
        "status": "error",
        "error_code": "moc_association_rejected",
    }
    assert moc_scope[0]["token"] not in bad.stdout + bad.stderr


def test_corrupt_moc_does_not_affect_search_or_unclassified_notes(config, moc_scope):
    path = config.state_root / "moc-associations.sqlite3"
    path.write_text("unusable MOC ledger")
    db = database.connect(config.state_root)
    try:
        results = search.search(config, db, moc_scope[0]["id"], "sentinel", [moc_scope[1]])
        assert results["results"][0]["note_id"] == moc_scope[4]
    finally:
        db.close()
    with pytest.raises((StorageError, sqlite3.Error)):
        run(config, moc_scope, "show")


def test_cross_vault_move_is_missing_without_adoption(config, moc_scope):
    run(config, moc_scope)
    link(config, moc_scope, owner="manual")
    (moc_scope[2] / "manual.md").rename(config.project_root / "test vault/temp_vault2/moved.md")
    result = run(config, moc_scope, "show")
    assert result["members"][0]["status"] == "missing"
    assert result["members"][0]["owners"] == ["manual"]
    assert result["members"][0]["note_path"] is None


def test_oversized_record_and_database_are_rejected(config, moc_scope):
    run(config, moc_scope)
    before = ledger(config)
    with patch.object(moc_store, "MAX_RECORD_BYTES", 1), pytest.raises(StorageError):
        run(config, moc_scope, "show")
    assert ledger(config) == before
    path = config.state_root / "moc-associations.sqlite3"
    with path.open("r+b") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    with pytest.raises(StorageError):
        run(config, moc_scope, "show")


def test_root_changes_at_last_check_prevent_sidecar_creation(config, moc_scope):
    read = moc_associations.checked_inventory
    calls = 0

    def changed(*args):
        nonlocal calls
        result = read(*args)
        calls += 1
        if calls == 2:
            root = moc_scope[2]
            root.rename(root.with_name("old"))
            root.mkdir()
        return result

    with (
        patch.object(moc_associations, "checked_inventory", side_effect=changed),
        pytest.raises(StorageError),
    ):
        run(config, moc_scope)
    assert not (config.state_root / "moc-associations.sqlite3").exists()


@pytest.mark.parametrize(
    "action,kwargs",
    [
        ("show", {"revision": 1}),
        ("show", {"owner": "system"}),
        (
            "link",
            {
                "revision": 1,
                "note_identity": "path:manual.md",
                "owner": "system",
                "expected_sha256": "0" * 64,
            },
        ),
        (
            "unlink-system",
            {"revision": 1, "note_identity": "note:" + str(uuid4()), "owner": "manual"},
        ),
    ],
)
def test_invalid_mutation_combinations_are_rejected(config, moc_scope, action, kwargs):
    with pytest.raises(StorageError):
        run(config, moc_scope, action, **kwargs)
    assert not (config.state_root / "moc-associations.sqlite3").exists()
