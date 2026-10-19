"""Merge real publication formats and retain identity, raw sources and expert canonical bytes."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from test_document_parts import chapters, processed
from test_media_jobs import media_scope as media_scope
from test_media_processing import complete
from test_media_publication import approved
from test_publication_layout import activate
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner
from test_source_imports import request, upload
from test_vault_merge_execution import finish

from fiber_bundle import (
    auth,
    backup_bundle,
    database,
    document_publication,
    expert_evidence,
    expert_publication,
    media_publication,
    note_inventory,
    note_provenance,
    search,
    vault_merge_execution,
    vault_merge_plan,
)
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.vault_save_contract import settings_wire, unified_settings


@pytest.fixture(autouse=True)
def explicit_progression(monkeypatch):
    # These tests advance the placement/merge phases explicitly; scheduler ownership
    # is exercised separately and must not race the exact byte-preservation checks.
    monkeypatch.setattr("fiber_bundle.automatic_ingestion.Runtime.start", lambda self: None)


def merge(config, cid, source, tmp_path):
    with database.connect(config.state_root) as db:
        target = db.execute("SELECT id FROM vaults WHERE id!=?", (source,)).fetchone()[0]
        for vid in (source, target):
            auth.set_grant(db, cid, vid, query=True, original=True, add=True)
        initial = note_inventory.read(config, db, auth.grant(db, cid, source, "query"))
    fields = settings_wire(unified_settings())
    fields.pop("layout_mode")
    activate(config, target, **fields)
    with database.connect(config.state_root) as db:
        packet = vault_merge_plan.preflight(
            config,
            db,
            vault_merge_plan.Selection(
                client_id=cid,
                source_vault_ids=[source],
                target_vault_id=target,
                target_grants=[
                    vault_merge_plan.Grant(client_id=cid, query=True, original=True, add=True)
                ],
            ),
        )
        assert packet["ready"], packet["conflicts"]
        frozen = vault_merge_plan.freeze(
            config,
            db,
            vault_merge_plan.Selection.model_validate(packet["selection"]),
            packet["plan_sha256"],
        )
    backup = (tmp_path / "backup").resolve()
    backup_bundle.create(config, backup, "synthetic-content-merge-password")
    vault_merge_execution.start(
        config, frozen["id"], packet["plan_sha256"], backup, "synthetic-content-merge-password"
    )
    finish(config, frozen["id"], packet)
    return target, initial, packet, frozen["id"]


def test_multipart_manifests_identities_and_all_units_survive_merge(config, owner, tmp_path):
    db, rid, item, meta, candidate = processed(config, owner, chapters())
    document_publication.publish_locked(config, db, rid)
    db.close()
    cid, _, ids = owner
    target, before, packet, _ = merge(config, cid, ids["personal"], tmp_path)
    with database.connect(config.state_root) as db:
        inventory = note_inventory.read(config, db, auth.grant(db, cid, target, "query"))
        assert {n.identity for n in inventory.notes} == {n.identity for n in before.notes}
        for n in inventory.notes:
            provenance = note_provenance.describe(
                config, db, cid, auth.grant(db, cid, target, "query"), inventory, n
            )
            if provenance.get("document_part", {}).get("role") == "extraction":
                assert provenance["source_kind"] == "published_document"
                assert provenance["originals"]
        for index in range(8):
            assert search.vault_search(config, db, cid, f"chapter-token-{index}", [target])[
                "results"
            ]
    root = Path(packet["inventories"][-1]["context"]["root"])
    assert list((root / "Attachments/pristin").iterdir())


def test_legacy_media_transcript_and_target_originals_survive_merge(config, media_scope, tmp_path):
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    cid, source = media_scope[0]["id"], media_scope[1]
    with database.connect(config.state_root) as db:
        media_publication.publish_locked(config, db, job)
    target, _, _, _ = merge(config, cid, source, tmp_path)
    with database.connect(config.state_root) as db:
        hit = search.vault_search(config, db, cid, "Literal synthetic caption", [target])[
            "results"
        ][0]
        assert hit["source_kind"] == "published_media" and hit["originals"]


def expert_package(config, owner, *, automatic=False):
    cid, _, ids = owner
    source = ids["personal"]
    fields = settings_wire(unified_settings())
    fields.pop("layout_mode")
    activate(config, source, **{**fields, "moc_archive_mode": "automatic"})
    with database.connect(config.state_root) as db:
        item = upload(
            config, db, request(cid, source, b"synthetic source", "book.txt"), b"synthetic source"
        )
        identity = item["id"]
        record = dict(db.execute("SELECT * FROM source_imports WHERE id=?", (identity,)).fetchone())
        record["plan_json"] = canonical(
            {"limits": {"markdown_bytes": 1024 * 1024, "markdown_total_bytes": 32 * 1024 * 1024}}
        )
        db.execute(
            "INSERT INTO source_expert_tasks(id,request_sha256,plan_sha256,sta"
            "te,updated_at) VALUES(?,?,?,'completed',0)",
            (identity, "a" * 64, "b" * 64),
        )
        packet = {"source_sha256": "c" * 64}
        files = {
            "references/knowledge/index.json": canonical(
                {"objects": [{"path": "one.json"}]}
            ).encode(),
            "one.json": canonical(
                {"id": "one", "title": "Chapter", "evidence_ids": ["a"]}
            ).encode(),
            "references/evidence/anchors.jsonl": b'{"id":"a","pages":[1]}\n',
        }
        with patch.object(expert_evidence, "load", return_value=(record, packet, files)):
            if automatic:
                from fiber_bundle import automatic_ingestion, automatic_jobs, automatic_policy

                automatic_policy.save(
                    config,
                    db,
                    source,
                    0,
                    automatic_policy.Policy(
                        client_id=cid,
                        review_mode="automatic",
                        publish_mode="automatic",
                        allowed_kinds=["expert"],
                    ),
                )
                job_id = automatic_jobs.enqueue(config, db, cid, source, "expert", identity)
                automatic_jobs.settle(db, automatic_jobs.get(db, job_id), "waiting", "review")
                for _ in range(3):
                    assert automatic_ingestion.tick(config)
                assert automatic_jobs.get(db, job_id)["state"] == "complete"
                from fiber_bundle.publication_locations import wire

                result = wire(db, "expert", identity)
            else:
                result = expert_publication.publish(
                    config, db, cid, identity, digest(canonical(packet).encode()), True, True
                )
    return result, files


def test_complete_book_is_byte_preserved_and_moc_never_moves_it(config, owner, tmp_path):
    from uuid import uuid4

    from fiber_bundle import automatic_placement, moc_store, note_placement

    result, canonical_files = expert_package(config, owner)
    cid, _, ids = owner
    source = ids["personal"]
    root = config.vault_path("test vault/temp_vault1")
    before = {
        p.relative_to(root / result["package_root"]).as_posix(): p.read_bytes()
        for p in (root / result["package_root"]).rglob("*")
        if p.is_file()
    }
    with database.connect(config.state_root) as db:
        note = next(
            n
            for n in note_inventory.read(config, db, auth.grant(db, cid, source, "query")).notes
            if n.relative_path == result["entry_note_path"]
        )
        moc_store.save(
            config,
            source,
            str(uuid4()),
            cid,
            moc_store.Record(
                "Expert topic",
                root.stat().st_dev,
                root.stat().st_ino,
                1,
                {note.identity: ["manual"]},
            ),
            "create",
            0,
        )
        queued = note_placement.prepare_after_moc(config, db, cid, source, [note.identity])
        assert queued
    assert automatic_placement.tick(config)
    assert all(
        (root / result["package_root"] / path).read_bytes() == raw for path, raw in before.items()
    )
    target, _, packet, _ = merge(config, cid, source, tmp_path)
    with database.connect(config.state_root) as db:
        location = db.execute(
            "SELECT package_root FROM publication_locations WHERE kind='expert'"
        ).fetchone()[0]
        assert location.startswith("Book/")
        out = Path(packet["inventories"][-1]["context"]["root"]) / location
        assert {
            p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()
        } == before
        hits = search.vault_search(config, db, cid, "Chapter", [target])["results"]
        assert hits and all(h["knowledge_verified"] is False for h in hits)
        assert all(h["review_state"] == "deferred" for h in hits)
        assert all(h["originals"] for h in hits)


def test_full_expert_automatic_draft_uses_system_credential_and_keeps_deferred_semantics(
    config, owner
):
    result, canonical_files = expert_package(config, owner, automatic=True)
    cid, _, ids = owner
    with database.connect(config.state_root) as db:
        notes = search.vault_search(config, db, cid, "Chapter", [ids["personal"]])["results"]
        assert notes and all(
            n["review_type"] == "system" and n["review_actor"] == "system_rules" for n in notes
        )
        assert all(
            n["knowledge_verified"] is False and n["review_state"] == "deferred" for n in notes
        )
        assessment = db.execute(
            "SELECT assessment_json FROM source_expert_publications"
        ).fetchone()[0]
        assert not {"source_reviewed", "candidate_acknowledged"} & json.loads(assessment).keys()
        assert result["package_root"].startswith("Book/")
