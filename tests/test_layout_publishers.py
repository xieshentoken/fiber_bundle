"""Real reviewed source paths with synthetic capture workers; never provider accuracy claims."""

from unittest.mock import patch

from test_document_evidence import completed as _document_completed
from test_document_evidence import document_scope as document_scope
from test_document_human_publication import allow, assessment
from test_execution import policy as policy
from test_media_jobs import media_scope as media_scope
from test_media_processing import complete
from test_media_publication import approved
from test_publication_layout import activate
from test_session_search import owner as _session_owner
from test_source_imports import owner as owner
from test_source_imports import request, upload

from fiber_bundle import (
    auth,
    backup_bundle,
    backup_restore,
    database,
    document_evidence,
    document_publication,
    document_review,
    expert_evidence,
    expert_publication,
    media_jobs,
    media_publication,
    note_inventory,
    note_placement,
    note_placement_execution,
    note_provenance,
    note_search_build,
    search,
    vault_save_policy,
)
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest

document_completed = _document_completed
session_owner = _session_owner


def test_document_publisher_uses_preview_entry_and_preserves_source(
    config, policy, document_scope, document_completed
):
    activate(
        config,
        document_scope[1],
        inbox_dir="00-Inbox",
        archive_dir="领域/资料",
        image_dir="06-Attachments/00-images",
    )
    with allow(config, document_scope) as db:
        _, candidate = document_evidence.load(config, db, document_scope[0], document_completed)
        document_review.record(db, document_completed, candidate, assessment(candidate))
        preview = document_publication.publish_locked(
            config, db, document_completed, preview_only=True
        )
        root = config.project_root / "test vault/temp_vault1"
        assert (
            preview["entry_note_path"].startswith("00-Inbox/")
            and not (root / preview["entry_note_path"]).exists()
        )
        assert (
            db.execute(
                "SELECT * FROM document_publications WHERE run_id=?", (document_completed,)
            ).fetchone()
            is None
        )
        result = document_publication.publish_locked(config, db, document_completed)
        assert result["entry_note_path"] == preview["entry_note_path"]
        assert (root / result["entry_note_path"]).is_file()
        assert document_publication.publish_locked(config, db, document_completed)["historical"]
        notes = note_inventory.read(config, db, auth.grant(db, *document_scope[:2], "query")).notes
        assert any(n.relative_path == result["entry_note_path"] and n.identity for n in notes)
        inventory = note_inventory.read(config, db, auth.grant(db, *document_scope[:2], "query"))
        note = next(n for n in inventory.notes if n.relative_path == result["entry_note_path"])
        provenance = note_provenance.describe(
            config,
            db,
            document_scope[0],
            auth.grant(db, *document_scope[:2], "query"),
            inventory,
            note,
        )
        spans = provenance["document_source_spans"]
        assert spans["matches_published"] and spans["published_note_sha256"] == note.sha256
        assert spans["published_units"] and spans["reviewed_units"]
        before = search.search(config, db, document_scope[0], note.title, [document_scope[1]])[
            "results"
        ]
        note_search_build.build(config, *document_scope[:2])
        after = search.search(config, db, document_scope[0], note.title, [document_scope[1]])[
            "results"
        ]
        assert before == after and any(r["note_path"] == note.relative_path for r in after)


def test_media_publisher_previews_and_replays_without_new_outputs(config, media_scope):
    activate(config, media_scope[1])
    job = complete(config, media_scope)
    approved(config, media_scope, job)
    with media_jobs.connection(config) as db:
        preview = media_publication.publish_locked(config, db, job, preview_only=True)
        root = config.project_root / "test vault/temp_vault1"
        assert (
            preview["entry_note_path"].startswith("Inbox/")
            and not (root / preview["entry_note_path"]).exists()
        )
        result = media_publication.publish_locked(config, db, job)
        assert result["entry_note_path"] == preview["entry_note_path"]
        assert not (root / ("Media-" + job)).exists()
        assert media_publication.publish_locked(config, db, job)["historical"]
        grant = auth.grant(db, media_scope[0]["id"], media_scope[1], "query")
        inventory = note_inventory.read(config, db, grant)
        current = next(n for n in inventory.notes if n.relative_path == result["entry_note_path"])
        provenance = note_provenance.describe(
            config, db, media_scope[0]["id"], grant, inventory, current
        )
        assert provenance["source_kind"] == "published_media" and provenance["originals"]
        before = search.search(config, db, media_scope[0]["id"], current.title, [media_scope[1]])[
            "results"
        ]
    note_search_build.build(config, media_scope[0]["id"], media_scope[1])
    with database.connect(config.state_root) as db:
        after = search.search(config, db, media_scope[0]["id"], current.title, [media_scope[1]])[
            "results"
        ]
        assert before == after and any(r["note_path"] == current.relative_path for r in after)


def test_expert_publisher_keeps_complete_canonical_tree_and_draft(config, owner):
    client, _, ids = owner
    vault = ids["personal"]
    activate(config, vault)
    with database.connect(config.state_root) as db:
        item = upload(
            config, db, request(client, vault, b"synthetic source", "book.txt"), b"synthetic source"
        )
        identity = item["id"]
        source = dict(db.execute("SELECT * FROM source_imports WHERE id=?", (identity,)).fetchone())
        source["plan_json"] = canonical(
            {"limits": {"markdown_bytes": 1024 * 1024, "markdown_total_bytes": 32 * 1024 * 1024}}
        )
        db.execute(
            "INSERT INTO source_expert_tasks(id,request_sha256,plan_sha256,state,updated_at) "
            "VALUES(?,?,?,'completed',0)",
            (identity, "a" * 64, "b" * 64),
        )
        packet = {"source_sha256": "c" * 64}
        obj = {"id": "one", "title": "Chapter", "evidence_ids": ["a"]}
        files = {
            "references/knowledge/index.json": canonical(
                {"objects": [{"path": "one.json"}]}
            ).encode(),
            "one.json": canonical(obj).encode(),
            "references/evidence/anchors.jsonl": b'{"id":"a","pages":[1]}\n',
        }
        with patch.object(expert_evidence, "load", return_value=(source, packet, files)):
            preview = expert_publication.publish(
                config,
                db,
                client,
                identity,
                digest(canonical(packet).encode()),
                True,
                True,
                preview_only=True,
            )
            result = expert_publication.publish(
                config, db, client, identity, digest(canonical(packet).encode()), True, True
            )
        assert result["review_state"] == "deferred" and result["knowledge_verified"] is False
        assert result["package_root"].startswith("Inbox/") and "-Draft" in result["package_root"]
        root = config.project_root / "test vault/temp_vault1" / result["package_root"]
        for name, raw in files.items():
            assert (root / ".fiber-expert-canonical" / name).read_bytes() == raw
        assert (root / "note.md").is_file() and (root / "object-0001.md").is_file()
        assert result["entry_note_path"] == preview["entry_note_path"]
        inv = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
        assert (
            len([n for n in inv.notes if n.relative_path.startswith(result["package_root"] + "/")])
            == 2
        )
        entry = next(n for n in inv.notes if n.relative_path == result["entry_note_path"])
        packet = note_placement.preview(config, db, client, vault, entry.identity, entry.sha256)
        assert packet["ready"], packet
        applied = note_placement_execution.apply(config, db, client, vault, packet["operation_id"])
        assert applied["state"] == "completed" and not root.exists()
        relocated = config.project_root / "test vault/temp_vault1" / applied["target_path"]
        for name, raw in files.items():
            assert (relocated / ".fiber-expert-canonical" / name).read_bytes() == raw


def test_new_layout_encrypted_backup_restore_rebinds_files_but_requires_rule_confirmation(
    config, policy, document_scope, document_completed, tmp_path
):
    activate(config, document_scope[1])
    with allow(config, document_scope) as db:
        _, candidate = document_evidence.load(config, db, document_scope[0], document_completed)
        document_review.record(db, document_completed, candidate, assessment(candidate))
        result = document_publication.publish_locked(config, db, document_completed)
    password = "synthetic-layout-backup-only"
    backup_bundle.create(config, tmp_path / "layout-bundle", password)
    backup_restore.restore(tmp_path / "layout-bundle", tmp_path / "restored", password)
    from fiber_bundle.config import load_config

    restored = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    backup_restore.activate(restored, source_stopped=True)
    with database.connect(restored.state_root) as db:
        assert not vault_save_policy.get(restored, db, document_scope[1])["publication_ready"]
        db.execute("UPDATE clients SET active=1 WHERE id=?", (document_scope[0],))
        inv = note_inventory.read(restored, db, auth.grant(db, *document_scope[:2], "query"))
        assert any(n.relative_path == result["entry_note_path"] for n in inv.notes)
