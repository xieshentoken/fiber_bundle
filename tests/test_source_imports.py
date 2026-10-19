"""Observable resumable import, zero-model extraction and safe publication behaviour."""

import base64
import sqlite3
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_api import permissions
from test_session_search import owner as session_owner  # noqa: F401
from test_source_processing import WORKERS, worker

from fiber_bundle import (
    auth,
    database,
    document_evidence,
    document_publication,
    document_review,
    execution_ledger,
    objects,
    source_processing_policy,
    source_task_policy,
)
from fiber_bundle import source_import_execution as execution
from fiber_bundle import source_imports as imports
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.store import Store


@pytest.fixture
def owner(http, request):
    cid, headers, ids = request.getfixturevalue("session_owner")
    permissions(http, cid, ids["personal"], query=True, original=True, add=True)
    return cid, headers, ids


def request(cid, vid, raw=b"hello", filename="note.txt", action="archive", sha=None):
    return dict(
        client_id=cid,
        vault_id=vid,
        submission_id=str(uuid4()),
        filename=filename,
        source_sha256=objects.digest(raw),
        source_bytes=len(raw),
        action=action,
        engine="ordinary",
        configuration_sha256=sha,
        overrides={},
    )


def upload(config, db, req, raw):
    item = imports.create(config, db, req)
    for ordinal, offset in enumerate(range(0, len(raw), imports.CHUNK_BYTES)):
        data = raw[offset : offset + imports.CHUNK_BYTES]
        imports.chunk(
            config,
            db,
            req["client_id"],
            item["id"],
            ordinal,
            objects.digest(data),
            base64.b64encode(data).decode(),
        )
    return imports.finish(config, db, req["client_id"], item["id"])


def test_archive_no_models_idempotent_and_new_intent(http, owner, config):
    cid, _, ids = owner
    vid = ids["personal"]
    raw = b"a" * (imports.CHUNK_BYTES + 13)
    req = request(cid, vid, raw, filename="same.bin")
    first = http.post("/admin/imports", json=req)
    assert first.status_code == 201
    identity = first.json()["id"]
    for ordinal, part in enumerate((raw[: imports.CHUNK_BYTES], raw[imports.CHUNK_BYTES :])):
        body = dict(
            client_id=cid,
            ordinal=ordinal,
            sha256=objects.digest(part),
            data=base64.b64encode(part).decode(),
        )
        for _ in range(2):
            response = http.put(f"/admin/imports/{identity}/chunks", json=body)
            assert response.status_code == 200
        changed = {
            **body,
            "data": base64.b64encode(b"b" * len(part)).decode(),
            "sha256": objects.digest(b"b" * len(part)),
        }
        assert http.put(f"/admin/imports/{identity}/chunks", json=changed).status_code == 422
    archived = http.post(f"/admin/imports/{identity}/finish", json={"client_id": cid})
    assert archived.status_code == 200
    assert archived.json()["state"] == "archived" and archived.json()["run_id"] is None
    assert (
        http.post(f"/admin/imports/{identity}/finish", json={"client_id": cid}).json()
        == archived.json()
    )
    assert http.post("/admin/imports", json=req).json()["id"] == identity
    db = database.connect(config.state_root)
    assert db.execute("select count(*) from model_calls").fetchone()[0] == 0
    assert Store(config).read_asset(archived.json()["asset_id"], vault="personal") == raw
    second = upload(config, db, {**req, "submission_id": str(uuid4())}, raw)
    assert second["receipt_id"] != archived.json()["receipt_id"]
    assert second["asset_id"] == archived.json()["asset_id"]
    db.close()


@pytest.mark.parametrize(
    "fault", ["digest", "order", "short", "incomplete", "tamper", "foreign", "cancel", "regrant"]
)
def test_upload_failures_and_scope(config, store, owner, fault):
    cid, _, ids = owner
    vid = ids["personal"]
    db = database.connect(config.state_root)
    raw = b"x" * 10
    req = request(cid, vid, raw)
    item = imports.create(config, db, req)
    if fault == "foreign":
        other = auth.create_client(db, "other", False)["id"]
        with pytest.raises(auth.Denied):
            imports.get(config, db, other, item["id"])
    elif fault == "cancel":
        imports.cancel(config, db, cid, item["id"])
        with pytest.raises(auth.Denied):
            imports.finish(config, db, cid, item["id"])
    elif fault == "regrant":
        db.execute(
            "update client_vault_grants set can_add=0 where client_id=? and vault_id=?", (cid, vid)
        )
        db.execute(
            "update client_vault_grants set can_add=1 where client_id=? and vault_id=?", (cid, vid)
        )
        with pytest.raises(auth.Denied):
            imports.finish(config, db, cid, item["id"])
        assert imports.listing(config, db, cid, vid) == []
    else:
        if fault in {"incomplete", "tamper"}:
            if fault == "tamper":
                imports.chunk(
                    config,
                    db,
                    cid,
                    item["id"],
                    0,
                    objects.digest(raw),
                    base64.b64encode(raw).decode(),
                )
                (config.state_root / "source-imports" / item["id"] / "0.part").write_bytes(
                    b"b" * 10
                )
            with pytest.raises((InvalidInput, IntegrityError)):
                imports.finish(config, db, cid, item["id"])
        else:
            with pytest.raises(InvalidInput):
                imports.chunk(
                    config,
                    db,
                    cid,
                    item["id"],
                    1 if fault == "order" else 0,
                    "a" * 64
                    if fault == "digest"
                    else objects.digest(raw[:5] if fault == "short" else raw),
                    base64.b64encode(raw[:5] if fault == "short" else raw).decode(),
                )
    assert db.execute("select count(*) from source_versions").fetchone()[0] == 0
    db.close()


def test_model_free_markdown_review_publication_and_frozen_policy(config, store, owner):
    cid, _, ids = owner
    vid = ids["personal"]
    db = database.connect(config.state_root)
    settings = source_processing_policy.save(config, None, WORKERS, None)
    raw = "# 标题\n\n正文 2.4 MPa\n\n```python\nprint(42)\n```\n".encode()
    req = request(cid, vid, raw, "note.md", "process", settings["configuration_sha256"])
    item = upload(config, db, req, raw)
    rid = execution.prepare(config, db, cid, item["id"])
    assert execution.prepare(config, db, cid, item["id"]) == rid
    run = execution_ledger.get_run(db, rid)
    assert run["binding_kind"] == "document" and run["task_limit_nusd"] == 0
    assert source_task_policy.frozen(db, rid)["format"] == "markdown"
    db.close()
    with patch("fiber_bundle.document_worker.execute_document", side_effect=worker):
        execution.execute(config, rid)
    db = database.connect(config.state_root)
    meta, candidate = document_evidence.load(config, db, cid, rid)
    assert meta["source_format"] == "markdown" and meta["missing_references"] == []
    assert candidate["segments"][0]["text"] == raw.decode()
    with pytest.raises(InvalidInput):
        document_publication.publish_locked(config, db, rid)
    assessment = dict(
        source_sha256=meta["source_sha256"],
        result_sha256=meta["result_sha256"],
        source_reviewed=True,
        visual_reviewed=True,
        judgments=[
            dict(unit_id="u1", verdict="accept", reason="synthetic literal and structure verified")
        ],
    )
    document_review.record(db, rid, candidate, assessment)
    result = document_publication.publish_locked(config, db, rid)
    assert result["state"] == "published"
    vault = config.vault_path("test vault/temp_vault1")
    note = (vault / result["relative_path"] / "note.md").read_text()
    assert raw.decode() in note and "```text" not in note
    assert imports.listing(config, db, cid, vid)[0]["state"] == "published"
    assert db.execute("select count(*) from model_calls").fetchone()[0] == 0
    db.close()


def test_http_guard_and_capabilities(http, owner):
    cid, _, ids = owner
    vid = ids["personal"]
    req = request(cid, vid, filename="note.pdf", action="process")
    assert http.post("/admin/imports", json=req).status_code == 422
    req.update(action="archive")
    for bad in ["../note.pdf", "dir/note.pdf"]:
        assert http.post("/admin/imports", json={**req, "filename": bad}).status_code == 422
    assert (
        http.post("/admin/imports", json=req, headers={"X-CSRF-Token": "invalid"}).status_code
        == 403
    )
    assert http.get("/admin/imports", params={"client_id": cid, "vault_id": vid}).json() == []


def test_markdown_missing_references_never_read_host():
    from fiber_bundle.source_import_markdown import references

    assert references(
        "![x](../../secret.png)\n![[asset.png]]\n[web](https://example.com)\n`![x](code.png)`"
    ) == ["../../secret.png", "asset.png"]


def test_v21_migration_backup_and_rollback(config, store):
    db = database.connect(config.state_root)
    db.execute("DROP TABLE source_expert_publications")
    db.execute("DROP TABLE source_expert_attempts")
    db.execute("DROP TABLE source_expert_tasks")
    db.execute("DROP TABLE source_import_chunks")
    db.execute("DROP TABLE source_imports")
    from fiber_bundle.document_schema import SCHEMA_V8

    db.execute("DROP TABLE document_tasks")
    db.executescript(SCHEMA_V8)
    from fiber_bundle.source_schema import SCHEMA_V20

    db.execute("DROP TABLE document_profiles")
    db.executescript(
        SCHEMA_V20.split("CREATE TABLE IF NOT EXISTS source_task_bindings")[0].replace(
            "INSERT INTO document_profiles_v20 SELECT * FROM document_profiles;\n"
            "DROP TABLE document_profiles;",
            "",
        )
    )
    from schema_support import remove_vault_catalog_schema

    remove_vault_catalog_schema(db)
    db.execute("PRAGMA user_version=21")
    before = [tuple(r) for r in db.execute("select * from vaults")]
    db.close()
    backup = database.migrate(config)
    assert backup is not None
    old = sqlite3.connect(backup)
    assert old.execute("pragma user_version").fetchone()[0] == 21
    old.close()
    db = database.connect(config.state_root)
    assert db.execute("pragma user_version").fetchone()[0] == database.SCHEMA_VERSION
    from schema_support import assert_preserved_rows

    assert_preserved_rows(db, "vaults", before)
    assert not db.execute("pragma foreign_key_check").fetchall()
    db.close()


@pytest.mark.parametrize("mapped", [True, False])
def test_attachment_mapping_frozen_review_and_publication(config, store, owner, mapped):
    cid, _, ids = owner
    vid = ids["personal"]
    db = database.connect(config.state_root)
    attachment = upload(config, db, request(cid, vid, b"image-data", "image.png"), b"image-data")
    settings = source_processing_policy.save(config, None, WORKERS, None)
    raw = b"# Note\n\n![image](assets/image.png)\n![[assets/image.png]]\n"
    req = request(cid, vid, raw, "note.md", "process", settings["configuration_sha256"])
    req["references"] = {"assets/image.png": attachment["id"]} if mapped else {}
    item = upload(config, db, req, raw)
    rid = execution.prepare(config, db, cid, item["id"])
    db.close()
    with patch("fiber_bundle.document_worker.execute_document", side_effect=worker):
        execution.execute(config, rid)
    db = database.connect(config.state_root)
    meta, candidate = document_evidence.load(config, db, cid, rid)
    document_review.record(
        db,
        rid,
        candidate,
        dict(
            source_sha256=meta["source_sha256"],
            result_sha256=meta["result_sha256"],
            source_reviewed=True,
            visual_reviewed=True,
            judgments=[
                dict(
                    unit_id="u1",
                    verdict="accept",
                    reason="original and explicit attachment mapping checked",
                )
            ],
        ),
    )
    if not mapped:
        assert imports.listing(config, db, cid, vid)[0]["state"] == "needs_supplement"
        assert meta["review_gate"]["publication_ready"] is False
        with pytest.raises(InvalidInput):
            document_publication.publish_locked(config, db, rid)
    else:
        published = document_publication.publish_locked(config, db, rid)
        root = config.vault_path("test vault/temp_vault1") / published["relative_path"]
        sha = objects.digest(b"image-data")
        assert (root / "assets" / f"{sha}.png").read_bytes() == b"image-data"
        assert "assets/" + sha + ".png" in (root / "note.md").read_text()
        assert "assets/image.png" not in (root / "note.md").read_text()
        # Tampering with frozen mapping invalidates the source binding; no overwrite/replay.
        stored = db.execute(
            "select request_json from source_imports where id=?", (item["id"],)
        ).fetchone()[0]
        db.execute(
            "update source_imports set request_json=? where id=?",
            (stored.replace(attachment["id"], str(uuid4())), item["id"]),
        )
        with pytest.raises(auth.Denied):
            document_evidence.load(config, db, cid, rid)
    db.close()


def test_no_whole_source_read_during_archive(config, store, owner):
    cid, _, ids = owner
    db = database.connect(config.state_root)
    with patch(
        "fiber_bundle.objects.read_object", side_effect=AssertionError("unexpected whole-file read")
    ):
        archived = upload(config, db, request(cid, ids["personal"]), b"hello")
    assert archived["state"] == "archived"
    db.close()


@pytest.mark.parametrize(
    "prefix", ["CREATE TABLE source_imports", "ALTER TABLE execution_runs_new", "COMMIT"]
)
def test_schema22_failure_rolls_back_every_old_row(config, store, monkeypatch, prefix):
    db = database.connect(config.state_root)
    db.execute("drop table source_import_chunks")
    db.execute("drop table source_imports")
    from fiber_bundle.document_schema import SCHEMA_V8

    db.execute("DROP TABLE document_tasks")
    db.executescript(SCHEMA_V8)
    from fiber_bundle.source_schema import SCHEMA_V20

    db.execute("DROP TABLE document_profiles")
    db.executescript(
        SCHEMA_V20.split("CREATE TABLE IF NOT EXISTS source_task_bindings")[0].replace(
            "INSERT INTO document_profiles_v20 SELECT * FROM document_profiles;\n"
            "DROP TABLE document_profiles;",
            "",
        )
    )
    from schema_support import remove_vault_catalog_schema

    remove_vault_catalog_schema(db)
    db.execute("pragma user_version=21")
    before = {
        r[0]: [tuple(x) for x in db.execute('select * from "' + r[0] + '"')]
        for r in db.execute("select name from sqlite_master where type='table'")
    }
    db.close()

    class Fault(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.strip().startswith(prefix):
                raise sqlite3.OperationalError("synthetic migration fault")
            return super().execute(sql, *args, **kwargs)

    def connect(root, **kwargs):
        result = sqlite3.connect(root / "metadata.sqlite3", isolation_level=None, factory=Fault)
        result.row_factory = sqlite3.Row
        return result

    monkeypatch.setattr(database, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate(config)
    db = sqlite3.connect(config.state_root / "metadata.sqlite3")
    assert db.execute("pragma user_version").fetchone()[0] == 21
    assert {
        r[0]: db.execute('select * from "' + r[0] + '"').fetchall()
        for r in db.execute("select name from sqlite_master where type='table'")
    } == before
    assert not db.execute("pragma foreign_key_check").fetchall()
    db.close()


def test_import_backup_restore_quarantines_pending_and_keeps_originals(
    config, store, owner, tmp_path
):
    from fiber_bundle import backup_bundle, backup_restore
    from fiber_bundle.config import load_config

    cid, _, ids = owner
    db = database.connect(config.state_root)
    pending = imports.create(config, db, request(cid, ids["personal"]))
    archived = upload(config, db, request(cid, ids["personal"], b"original"), b"original")
    db.close()
    backup_bundle.create(config, tmp_path / "bundle", "synthetic-only-password")
    result = backup_restore.restore(
        tmp_path / "bundle", tmp_path / "restored", "synthetic-only-password"
    )
    assert result["original_hashes_verified"] and not result["automatic_start"]
    restored = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    backup_restore.activate(restored, source_stopped=True)
    db = database.connect(restored.state_root)
    assert (
        db.execute("select state from source_imports where id=?", (pending["id"],)).fetchone()[0]
        == "interrupted"
    )
    assert (
        db.execute("select state from source_imports where id=?", (archived["id"],)).fetchone()[0]
        == "archived"
    )
    assert db.execute("select active from clients where id=?", (cid,)).fetchone()[0] == 0
    assert db.execute("select count(*) from model_calls").fetchone()[0] == 0
    db.close()
    assert Store(restored).read_asset(archived["asset_id"], vault="personal") == b"original"


def test_launch_unknown_does_not_retry_or_enable_model(config, store, owner):
    cid, _, ids = owner
    db = database.connect(config.state_root)
    settings = source_processing_policy.save(config, None, WORKERS, None)
    item = upload(
        config,
        db,
        request(
            cid, ids["personal"], b"hello", "note.txt", "process", settings["configuration_sha256"]
        ),
        b"hello",
    )
    with patch(
        "fiber_bundle.source_import_execution.subprocess.Popen",
        side_effect=OSError("synthetic launch failure"),
    ) as start:
        with pytest.raises(OSError):
            execution.launch(config, db, cid, item["id"])
        retry = execution.launch(config, db, cid, item["id"])
        assert retry["state"] == "interrupted" and start.call_count == 1
    assert db.execute("select count(*) from model_calls").fetchone()[0] == 0
    db.close()


def test_child_crash_is_interrupted_not_relaunched(config, store, owner):
    cid, _, ids = owner
    db = database.connect(config.state_root)
    settings = source_processing_policy.save(config, None, WORKERS, None)
    item = upload(
        config,
        db,
        request(
            cid, ids["personal"], b"hello", "note.txt", "process", settings["configuration_sha256"]
        ),
        b"hello",
    )
    rid = execution.prepare(config, db, cid, item["id"])
    db.close()

    class Exited:
        def wait(self):
            return -9

    execution._watch(config, rid, Exited())
    db = database.connect(config.state_root)
    assert execution_ledger.get_run(db, rid)["state"] == "interrupted"
    assert (
        db.execute("select state from document_tasks where run_id=?", (rid,)).fetchone()[0]
        == "interrupted"
    )
    with patch("fiber_bundle.source_import_execution.subprocess.Popen") as start:
        assert execution.launch(config, db, cid, item["id"])["state"] == "interrupted"
        start.assert_not_called()
    db.close()


def test_cancel_cleans_uncommitted_chunk_and_creation_retry_recovers_directory(
    config, store, owner
):
    cid, _, ids = owner
    db = database.connect(config.state_root)
    req = request(cid, ids["personal"])
    item = imports.create(config, db, req)
    root = config.state_root / "source-imports" / item["id"]
    root.rmdir()
    assert imports.create(config, db, req)["id"] == item["id"] and root.is_dir()
    (root / "0.part").write_bytes(b"partial")
    imports.cancel(config, db, cid, item["id"])
    assert not (root / "0.part").exists()
    db.close()


def test_original_stream_releases_lease_and_stops_on_revocation(http, owner, config, store):
    from fiber_bundle.document_bindings import source
    from fiber_bundle.source_import_delivery import chunks

    cid, _, ids = owner
    raw = b"x" * (imports.CHUNK_BYTES * 3)
    db = database.connect(config.state_root)
    imported = upload(config, db, request(cid, ids["personal"], raw), raw)
    file = source(config, db, cid, imported["receipt_id"], imported["asset_id"]).file
    stream = chunks(config, imported["id"], cid, file, http.cookies.get(auth.COOKIE))
    assert next(stream) == raw[: imports.CHUNK_BYTES]
    # A paused HTTP consumer holds its file handle but not the global storage lease.
    with database.locked(config):
        db.execute(
            "UPDATE client_vault_grants SET can_original=0 WHERE client_id=? AND vault_id=?",
            (cid, ids["personal"]),
        )
    with pytest.raises(auth.Denied):
        next(stream)
    stream.close()
    db.close()
