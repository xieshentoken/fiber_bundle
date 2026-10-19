"""Mixed provenance, encrypted restore and real managed MOC journals through a merge."""

from pathlib import Path

from test_article_tasks import article_scope as article_scope
from test_channel_intake import entry as entry
from test_document_creation_config import creation_policy as creation_policy
from test_document_creation_execution import prepared_task as prepared_task
from test_document_creation_lifecycle import attached as attached
from test_document_creation_tasks import input_task as input_task
from test_execution import policy as policy
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner
from test_vault_merge_content import merge
from test_vault_merge_execution import finish, start
from test_weixin_inbox import binding as binding

from fiber_bundle import (
    auth,
    backup_bundle,
    backup_restore,
    database,
    search,
    vault_merge_execution,
    vault_merge_recovery,
)
from fiber_bundle import document_creation_save as save
from fiber_bundle.config import load_config
from fiber_bundle.objects import digest


def test_saved_analysis_stays_nonindependent_after_explicit_audience_merge(
    config, owner, prepared_task, creation_policy, tmp_path
):
    from test_document_creation_rounds import finish_rounds, output_file

    from fiber_bundle import document_creation_artifacts

    active = attached.__wrapped__(config, owner, prepared_task, creation_policy)
    row = next(active)
    db, policy, guard, attempt = row
    finish_rounds(config, row)
    raw = output_file(config, row, tmp_path)
    document_creation_artifacts.register(config, db, attempt, policy, guard, tmp_path)
    active.close()  # The real completed runtime relinquishes its operation lock.
    db = database.connect(config.state_root)
    source = owner[2]["personal"]
    save.prepare(
        config,
        db,
        attempt.client_id,
        attempt.task_id,
        source,
        expected_artifact_sha256=digest(raw),
        allow_save=True,
    )
    save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    target, _, _, _ = merge(config, attempt.client_id, source, tmp_path)
    with database.connect(config.state_root) as side:
        results = search.vault_search(config, side, attempt.client_id, "100 psi", [target])[
            "results"
        ]
        assert results and all(r["source_kind"] == "agent_analysis" for r in results)
        assert all(r["independent_evidence"] is False and not r["originals"] for r in results)

    from test_publication_layout import activate

    from fiber_bundle import vault_catalog, vault_merge_plan
    from fiber_bundle.vault_save_contract import settings_wire, unified_settings

    third_root = tmp_path / "third-vault"
    third_root.mkdir()
    with database.connect(config.state_root) as db:
        admission = vault_catalog.preview(
            config,
            db,
            name="Third synthetic Vault",
            root=str(third_root),
            scope={"include": [""], "exclude": []},
            read_only=False,
        )
        third = vault_catalog.attach(config, db, admission["preview_id"])["vault_id"]
        auth.set_grant(db, attempt.client_id, third, query=True, original=True, add=True)
    fields = settings_wire(unified_settings())
    fields.pop("layout_mode")
    activate(config, third, **fields)
    with database.connect(config.state_root) as db:
        selection = vault_merge_plan.Selection(
            client_id=attempt.client_id,
            source_vault_ids=[target],
            target_vault_id=third,
            target_grants=[
                vault_merge_plan.Grant(
                    client_id=attempt.client_id, query=True, original=True, add=True
                )
            ],
        )
        second = vault_merge_plan.preflight(config, db, selection)
        assert second["ready"], second["conflicts"]
        frozen = vault_merge_plan.freeze(config, db, selection, second["plan_sha256"])
    bundle = tmp_path / "second-backup"
    backup_bundle.create(config, bundle, "synthetic-second-analysis-merge")
    vault_merge_execution.start(
        config, frozen["id"], second["plan_sha256"], bundle, "synthetic-second-analysis-merge"
    )
    finish(config, frozen["id"], second)
    with database.connect(config.state_root) as db:
        results = search.vault_search(config, db, attempt.client_id, "100 psi", [third])["results"]
        assert results and all(
            r["source_kind"] == "agent_analysis" and not r["independent_evidence"] for r in results
        )


def test_completed_merge_full_backup_restores_single_current_body_and_originals(
    config, article_scope, policy, tmp_path
):
    cid, _, target, _, identity, packet = start(config, article_scope, policy, tmp_path)
    finish(config, identity, packet)
    bundle, destination = tmp_path / "merged-backup", tmp_path / "restored"
    backup_bundle.create(config, bundle, "synthetic-completed-merge-restore")
    backup_restore.restore(bundle, destination, "synthetic-completed-merge-restore")
    restored = load_config(destination / "config.test.json", destination)
    backup_restore.activate(restored, source_stopped=True)
    with database.connect(restored.state_root) as db:
        db.execute("UPDATE clients SET active=1 WHERE id=?", (cid,))
    from fiber_bundle.note_search_build import build

    build(restored, cid, target)
    with database.connect(restored.state_root) as db:
        hits = search.vault_search(restored, db, cid, "Synthetic", [target])["results"]
        assert len(hits) == 1 and hits[0]["originals"]
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_managed_moc_keeps_human_body_members_and_reversible_journal(
    config, article_scope, policy, tmp_path
):
    from test_moc_dispatch import dispatch, moc, schedule
    from test_vault_merge_plan import prepared

    from fiber_bundle import moc_store, vault_merge_plan

    cid, source, target, pid, selection = prepared(config, article_scope, policy)
    root = config.vault_path("test vault/temp_vault1")
    with database.connect(config.state_root) as db:
        token = "synthetic-merge-moc-token"
        db.execute("UPDATE clients SET token_digest=? WHERE id=?", (auth.digest(token), cid))
        note = next(root.glob("article-*/note.md"))
        rid = db.execute(
            "SELECT run_id FROM article_publication_runs WHERE publication_id=?", (pid,)
        ).fetchone()[0]
    scope = {
        "authorization": "Bearer " + token,
        "vid": source,
        "cid": cid,
        "pid": pid,
        "rid": rid,
        "root": root,
        "note": note,
    }
    (root / "manual.md").write_text("# Manual human topic\n")
    selection.historical_files = {source: ["manual.md"]}
    mid, path = moc(config, scope)
    schedule(config, scope, (mid, path))
    assert dispatch(config, scope)["results"][0]["moc_file_write_performed"] is True
    human = (root / path).read_text().split("<!--")[0]
    with database.connect(config.state_root) as db:
        packet = vault_merge_plan.preflight(config, db, selection)
        assert packet["ready"], packet["conflicts"]
        frozen = vault_merge_plan.freeze(config, db, selection, packet["plan_sha256"])
    bundle = tmp_path / "moc-backup"
    backup_bundle.create(config, bundle, "synthetic-moc-merge-backup")
    vault_merge_execution.start(
        config, frozen["id"], packet["plan_sha256"], bundle, "synthetic-moc-merge-backup"
    )
    finish(config, frozen["id"], packet)
    current = moc_store.load(config, target, mid)
    assert current and current.members == {"publication:" + pid: ["system"]}
    target_path = next(f["target_path"] for f in packet["files"] if f["source_path"] == path)
    assert (
        (Path(packet["inventories"][-1]["context"]["root"]) / target_path)
        .read_text()
        .startswith(human.replace("[[manual]]", "[[Inbox/Manual human topic]]"))
    )
    vault_merge_recovery.rollback(config, frozen["id"], packet["plan_sha256"])
    assert not moc_store.load(config, target, mid)
    assert moc_store.load(config, source, mid).members == current.members


def test_legacy_intake_publication_merges_sidecar_provenance_and_original(
    config, entry, monkeypatch, tmp_path
):
    from test_article_fetch import Reply, install
    from test_articles import PNG, TEXT, X_URL, current_html
    from test_channel_intake import enable, receive
    from test_channel_intake_content import grant_original

    from fiber_bundle.channels import intake_extraction, intake_processing, intake_reviews

    enable(config, entry, fetch_articles=True, download_limit=5)
    batch = receive(config, entry, text=X_URL)
    intake_processing.advance(config, entry[0]["id"])
    install(
        monkeypatch,
        [
            Reply(200, current_html(), [("Content-Type", "text/html; charset=utf-8")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    intake_extraction.advance(config, entry[0]["id"])
    grant_original(config, entry)
    packet = intake_reviews.packet(config, entry[0]["id"], batch, 0)
    review = intake_reviews.review(
        config, entry[0]["id"], batch, 0, packet["digest"], "Synthetic complete check"
    )
    intake_reviews.publish(config, entry[0]["id"], batch, 0, review["id"])
    cid, source = entry[1].client_id, entry[1].vault_id
    target, _, _, _ = merge(config, cid, source, tmp_path)
    with database.connect(config.state_root) as db:
        results = search.vault_search(config, db, cid, TEXT[:12], [target])["results"]
        assert results and results[0]["source_kind"] == "published_channel_content"
        assert results[0]["originals"] and results[0]["review_actor"] == "local_admin"
