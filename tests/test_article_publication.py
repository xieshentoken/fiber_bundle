import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from test_article_tasks import article_scope as article_scope
from test_article_tasks import call, fixture_worker, start
from test_articles import current_html
from test_execution import policy as policy

from fiber_bundle import article_publication as publication
from fiber_bundle import article_tasks, article_worker, auth, database, publication_files
from fiber_bundle.article_capture import capture
from fiber_bundle.article_fetch import Response
from fiber_bundle.articles import ArticleError
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.operations import original


def completed(config, policy, scope, fetch=None):
    rid = start(config, policy, scope)
    if fetch is None:
        with patch.object(article_worker, "execute_capture", side_effect=fixture_worker):
            call(config, policy, rid)
    else:
        db = database.connect(config.state_root)
        task = db.execute("SELECT * FROM article_tasks WHERE run_id=?", (rid,)).fetchone()
        output = article_tasks.output_path(config, task)
        output.mkdir(parents=True)
        capture(task["source_url"], output, fetch)
        from fiber_bundle.objects import digest

        db.execute(
            "UPDATE article_tasks SET state='captured',manifest_sha256=?,image_count=1,"
            "markdown_bytes=1 WHERE run_id=?",
            (digest((output / "capture.json").read_bytes()), rid),
        )
        db.close()
    db = database.connect(config.state_root)
    db.execute("UPDATE execution_runs SET state='succeeded' WHERE id=?", (rid,))
    db.close()
    return rid


def rows(config, query, args=()):
    db = database.connect(config.state_root)
    try:
        return db.execute(query, args).fetchall()
    finally:
        db.close()


def destination(config, result):
    return config.project_root / "test vault/temp_vault1" / result["note_path"]


def test_publish_full_note_images_originals_and_idempotence(config, policy, article_scope):
    rid = completed(config, policy, article_scope)
    result = publication.publish(config, article_scope[0], rid)
    note = destination(config, result)
    assert result["published"] and note.is_file()
    assert "images/" in note.read_text()
    assert len(list((note.parent / "images").iterdir())) == 1
    assert publication.publish(config, article_scope[0], rid) == result
    assert len(rows(config, "SELECT * FROM article_publications")) == 1
    db = database.connect(config.state_root)
    asset = db.execute(
        "SELECT asset_id FROM version_assets WHERE version_id=? AND original_name='source.html'",
        (result["archive_version_id"],),
    ).fetchone()[0]
    with pytest.raises(auth.Denied):
        original(
            config,
            db,
            article_scope[0],
            article_scope[1],
            result["archive_version_id"],
            asset,
            1024 * 1024,
        )
    auth.set_grant(db, article_scope[0], article_scope[1], query=False, original=True, add=True)
    raw = original(
        config,
        db,
        article_scope[0],
        article_scope[1],
        result["archive_version_id"],
        asset,
        1024 * 1024,
    )
    assert raw == current_html()
    db.close()


def test_dynamic_html_dedup_keeps_each_submission_original(config, policy, article_scope):
    from test_article_capture import fixtures
    from test_articles import X_URL

    def dynamic(url, limit):
        response = fixtures(url, limit)
        if url == X_URL:
            return Response(url, response.content_type, response.data + b"<!-- dynamic nonce -->")
        return response

    first = publication.publish(config, article_scope[0], completed(config, policy, article_scope))
    second = publication.publish(
        config, article_scope[0], completed(config, policy, article_scope, dynamic)
    )
    assert first["publication_id"] == second["publication_id"]
    assert first["archive_version_id"] != second["archive_version_id"]
    assert first["archive_receipt_id"] != second["archive_receipt_id"]
    assert first["source_version_id"] == second["source_version_id"]
    assert len(rows(config, "SELECT * FROM article_publication_runs")) == 2
    assert len(list(destination(config, first).parent.parent.glob("article-*"))) == 1


def test_changed_content_creates_new_directory_old_bytes_untouched(config, policy, article_scope):
    from test_article_capture import fixtures
    from test_articles import X_URL

    def changed(url, limit):
        response = fixtures(url, limit)
        if url == X_URL:
            return Response(
                url,
                response.content_type,
                response.data.replace(b"Synthetic X", b"Changed title"),
            )
        return response

    first = publication.publish(config, article_scope[0], completed(config, policy, article_scope))
    old = destination(config, first).read_bytes()
    second = publication.publish(
        config, article_scope[0], completed(config, policy, article_scope, changed)
    )
    assert first["publication_id"] != second["publication_id"]
    assert destination(config, first).read_bytes() == old
    assert "Changed title" in destination(config, second).read_text()
    # Content returning to an earlier version reuses that publication, not a third note.
    third = publication.publish(config, article_scope[0], completed(config, policy, article_scope))
    assert third["publication_id"] == first["publication_id"]


@pytest.mark.parametrize("state", ["running", "cancelled", "failed", "interrupted"])
def test_execution_not_successful_refuses(config, policy, article_scope, state):
    rid = completed(config, policy, article_scope)
    db = database.connect(config.state_root)
    db.execute("UPDATE execution_runs SET state=? WHERE id=?", (state, rid))
    db.close()
    with pytest.raises(InvalidInput):
        publication.publish(config, article_scope[0], rid)
    assert rows(config, "SELECT * FROM article_publications") == []


@pytest.mark.parametrize("mode", ["revoke_add", "revoke_client", "other_client", "source_mutated"])
def test_authorization_before_publish(config, policy, article_scope, mode):
    rid = completed(config, policy, article_scope)
    db = database.connect(config.state_root)
    cid = article_scope[0]
    if mode == "revoke_add":
        auth.set_grant(db, cid, article_scope[1], query=True, original=True, add=False)
    elif mode == "revoke_client":
        db.execute("UPDATE clients SET active=0 WHERE id=?", (cid,))
    elif mode == "other_client":
        other = auth.create_client(db, "other", False)
        cid = other["id"]
        auth.set_grant(db, cid, article_scope[1], query=True, original=True, add=True)
    else:
        db.execute(
            "UPDATE article_tasks SET source_url='https://example.com/changed' WHERE run_id=?",
            (rid,),
        )
    db.close()
    with pytest.raises(auth.Denied):
        publication.publish(config, cid, rid)
    assert rows(config, "SELECT * FROM article_publications") == []


@pytest.mark.parametrize("name", ["capture.json", "source.html", "image-001.bin"])
def test_capture_mutation_never_publishes(config, policy, article_scope, name):
    rid = completed(config, policy, article_scope)
    task = rows(config, "SELECT * FROM article_tasks WHERE run_id=?", (rid,))[0]
    path = article_tasks.output_path(config, task) / name
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises((IntegrityError, ArticleError)):
        publication.publish(config, article_scope[0], rid)
    assert rows(config, "SELECT * FROM article_publications") == []


def test_crash_after_rename_reconciles_without_rewrite(config, policy, article_scope):
    rid = completed(config, policy, article_scope)
    rename = publication_files.rename_exclusive

    def crash(*args):
        rename(*args)
        raise SystemExit("synthetic process interruption")

    with patch.object(publication_files, "rename_exclusive", side_effect=crash):
        with pytest.raises(SystemExit):
            publication.publish(config, article_scope[0], rid)
    row = rows(config, "SELECT * FROM article_publications")[0]
    assert row["state"] == "ready"
    path = config.project_root / "test vault/temp_vault1" / row["relative_path"] / "note.md"
    inode = path.stat().st_ino
    with patch.object(
        publication_files, "rename_exclusive", side_effect=AssertionError("replayed")
    ):
        result = publication.publish(config, article_scope[0], rid)
    assert result["published"] and path.stat().st_ino == inode


def test_partial_stage_retry_preserves_partial_bytes(config, policy, article_scope):
    rid = completed(config, policy, article_scope)
    write = publication_files.write_new

    def full_disk(root, name, data):
        write(root, name, data[:8])
        raise OSError(28, "synthetic full disk")

    with patch.object(publication_files, "write_new", side_effect=full_disk):
        with pytest.raises(OSError):
            publication.publish(config, article_scope[0], rid)
    row = rows(config, "SELECT * FROM article_publications")[0]
    vault = config.project_root / "test vault/temp_vault1"
    partial = vault / row["staging_path"] / "note.md"
    assert partial.stat().st_size == 8 and not (vault / row["relative_path"]).exists()
    assert publication.publish(config, article_scope[0], rid)["published"]
    assert partial.stat().st_size == 8


@pytest.mark.parametrize("empty", [True, False])
def test_atomic_rename_refuses_existing_even_empty_directory(tmp_path, empty):
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "new").write_text("new")
    (tmp_path / "target").mkdir()
    if not empty:
        (tmp_path / "target" / "human").write_text("human")
    inode = (tmp_path / "target").stat().st_ino
    with pytest.raises(FileExistsError):
        publication_files.rename_exclusive(tmp_path, "source", "target")
    assert (tmp_path / "source" / "new").read_text() == "new"
    assert (tmp_path / "target").stat().st_ino == inode
    assert not (tmp_path / "target" / "new").exists()


@pytest.mark.parametrize("change", ["edit", "delete", "symlink", "extra"])
def test_human_changes_are_preserved_and_not_recreated(config, policy, article_scope, change):
    rid = completed(config, policy, article_scope)
    result = publication.publish(config, article_scope[0], rid)
    note = destination(config, result)
    if change == "edit":
        note.write_text("human edit")
    elif change == "delete":
        note.unlink()
    elif change == "symlink":
        note.unlink()
        note.symlink_to(config.project_root / "missing")
    else:
        (note.parent / "human.txt").write_text("human")
    with pytest.raises((IntegrityError, InvalidInput, ArticleError, OSError)):
        publication.publish(config, article_scope[0], rid)
    if change == "edit":
        assert note.read_text() == "human edit"
    elif change == "delete":
        assert not note.exists()
    elif change == "symlink":
        assert note.is_symlink()
    else:
        assert (note.parent / "human.txt").read_text() == "human"


def test_concurrent_publish_one_directory(config, policy, article_scope):
    rid = completed(config, policy, article_scope)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: publication.publish(config, article_scope[0], rid), [1, 2])
        )
    assert results[0] == results[1]
    assert len(rows(config, "SELECT * FROM article_publications")) == 1


def test_other_test_vault_uses_only_bound_target(config, policy, article_scope):
    from test_articles import X_URL

    from fiber_bundle.inputs import AssetInput
    from fiber_bundle.store import Store

    cid = article_scope[0]
    db = database.connect(config.state_root)
    vid = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
    auth.set_grant(db, cid, vid, query=False, original=False, add=True)
    receipt = Store(config).ingest_locked(
        db,
        ingress_scope="synthetic",
        event_id="supplier",
        vault="supplier",
        source_key="url",
        raw_event=b"supplier event",
        items=(AssetInput(X_URL.encode(), "url", "text/uri-list", "body"),),
        session_scope="synthetic",
        session_id="supplier",
        owner_client_id=cid,
    )
    db.close()
    scope = (cid, vid, {"job_id": receipt.job_id, "source_version_id": receipt.source_version_id})
    result = publication.publish(config, cid, completed(config, policy, scope))
    assert (config.project_root / "test vault/temp_vault2" / result["note_path"]).is_file()
    assert list((config.project_root / "test vault/temp_vault1").iterdir()) == []


def test_v4_migration_backup_preserves_rows_and_rolls_back_failure(config):
    config.state_root.mkdir()
    path = config.state_root / "metadata.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(database.SCHEMA + database.SCHEMA_V2 + database.SCHEMA_V3 + database.SCHEMA_V4)
    db.execute("INSERT INTO vaults(id,name,relative_path) VALUES('v','fixture','fixture')")
    db.execute("PRAGMA user_version=4")
    db.commit()
    db.close()
    with patch.object(database, "SCHEMA_V5", database.SCHEMA_V5 + "INVALID SQL;"):
        with pytest.raises(sqlite3.Error):
            database.migrate(config)
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 4
    assert len(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()) == 14
    db.close()
    backup = database.migrate(config)
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 4
    db = database.connect(config.state_root)
    assert db.execute("SELECT name FROM vaults").fetchone()[0] == "fixture"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.execute("SELECT count(*) FROM article_publications").fetchone()[0] == 0
    db.close()


def test_cli_authenticates_and_prints_no_note_body(config, policy, article_scope, capsys):
    from fiber_bundle import publication_cli

    rid = completed(config, policy, article_scope)
    token = config.project_root / "token"
    token.write_text("not-an-authorized-token")
    with patch(
        "sys.argv",
        [
            "fiber-publish",
            "--project-root",
            str(config.project_root),
            "--config",
            "config.json",
            "--run-id",
            rid,
            "--token-file",
            str(token),
        ],
    ):
        assert publication_cli.main() == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "publication_rejected"
    assert rows(config, "SELECT * FROM article_publications") == []


def test_real_process_exit_after_rename_recovers(config, policy, article_scope):
    import subprocess
    import sys

    rid = completed(config, policy, article_scope)
    script = """
import os, sys
from pathlib import Path
from fiber_bundle.config import load_config
from fiber_bundle import article_publication, publication_files
config = load_config(Path(sys.argv[1]) / 'config.json', Path(sys.argv[1]))
rename = publication_files.rename_exclusive
def interrupted(*args):
    rename(*args)
    os._exit(77)
publication_files.rename_exclusive = interrupted
article_publication.publish(config, sys.argv[2], sys.argv[3])
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(config.project_root), article_scope[0], rid],
        capture_output=True,
        timeout=30,
    )
    assert process.returncode == 77, process.stderr.decode()
    row = rows(config, "SELECT * FROM article_publications")[0]
    assert row["state"] == "ready"
    result = publication.publish(config, article_scope[0], rid)
    assert result["published"]
    assert len(rows(config, "SELECT * FROM article_publications")) == 1


def test_permission_rechecked_after_staging(config, policy, article_scope):
    rid = completed(config, policy, article_scope)
    stage = publication_files.stage

    def revoked(*args):
        stage(*args)
        db = database.connect(config.state_root)
        auth.set_grant(
            db, article_scope[0], article_scope[1], query=False, original=False, add=False
        )
        db.close()

    with patch.object(publication_files, "stage", side_effect=revoked):
        with pytest.raises(auth.Denied):
            publication.publish(config, article_scope[0], rid)
    row = rows(config, "SELECT * FROM article_publications")[0]
    vault = config.project_root / "test vault/temp_vault1"
    assert row["state"] == "ready"
    assert not (vault / row["relative_path"]).exists()
    assert (vault / row["staging_path"]).is_dir()


def test_replaced_vault_refused_before_retry(config, policy, article_scope):
    rid = completed(config, policy, article_scope)
    with patch.object(publication_files, "stage", side_effect=OSError("interruption")):
        with pytest.raises(OSError):
            publication.publish(config, article_scope[0], rid)
    vault = config.project_root / "test vault/temp_vault1"
    vault.rename(vault.with_name("preserved-vault"))
    vault.mkdir()
    with pytest.raises(IntegrityError, match="directory identity"):
        publication.publish(config, article_scope[0], rid)
    assert list(vault.iterdir()) == []


def test_ambiguous_missing_renamed_directory_not_recreated(config, policy, article_scope):
    rid = completed(config, policy, article_scope)
    rename = publication_files.rename_exclusive

    def interrupted(*args):
        rename(*args)
        raise SystemExit()

    with patch.object(publication_files, "rename_exclusive", side_effect=interrupted):
        with pytest.raises(SystemExit):
            publication.publish(config, article_scope[0], rid)
    row = rows(config, "SELECT * FROM article_publications")[0]
    vault = config.project_root / "test vault/temp_vault1"
    target = vault / row["relative_path"]
    target.rename(vault / "human-moved")
    with pytest.raises(IntegrityError, match="directory missing"):
        publication.publish(config, article_scope[0], rid)
    assert not target.exists()
    assert (vault / "human-moved" / "note.md").is_file()


def test_new_submission_does_not_overwrite_edited_existing_note(config, policy, article_scope):
    first = publication.publish(config, article_scope[0], completed(config, policy, article_scope))
    note = destination(config, first)
    note.write_text("human note")
    second_run = completed(config, policy, article_scope)
    with pytest.raises((IntegrityError, ArticleError)):
        publication.publish(config, article_scope[0], second_run)
    assert note.read_text() == "human note"
    assert len(rows(config, "SELECT * FROM article_publication_runs")) == 2
    assert len(rows(config, "SELECT * FROM article_publications")) == 1


def test_cli_success_uses_current_owner_token(config, policy, article_scope, capsys):
    from fiber_bundle import publication_cli

    rid = completed(config, policy, article_scope)
    # Local synthetic credential; never a provider key.
    token = config.project_root / "client-token"
    token.write_text("synthetic-publisher-token")
    db = database.connect(config.state_root)
    db.execute(
        "UPDATE clients SET token_digest=? WHERE id=?",
        (auth.digest(token.read_text()), article_scope[0]),
    )
    db.close()
    with patch(
        "sys.argv",
        [
            "fiber-publish",
            "--project-root",
            str(config.project_root),
            "--config",
            "config.json",
            "--run-id",
            rid,
            "--token-file",
            str(token),
        ],
    ):
        assert publication_cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["published"] and destination(config, result).is_file()
