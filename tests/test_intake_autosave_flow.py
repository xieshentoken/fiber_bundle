"""Both extraction modes reach searchable notes through automatic or explicit batch admission."""

import json
import posixpath
from urllib.parse import unquote

import pytest
from test_article_fetch import Reply, install
from test_articles import PNG
from test_channel_intake import enable, receive, records
from test_channel_intake_content import grant_original
from test_publication_layout import activate
from test_wechat_image_messages import URL, image_message_html

from fiber_bundle import (
    automatic_ingestion,
    automatic_policy,
    database,
    execution_ledger,
    note_search_build,
    note_search_cache,
    pi_processing,
    publication_links,
    search,
    vault_save_contract,
)
from fiber_bundle.channels import intake_extraction, intake_processing, intake_reviews
from fiber_bundle.channels import store as channel_store
from fiber_bundle.model_configuration import store as catalog
from fiber_bundle.model_configuration.contracts import Save

pytest_plugins = ["test_channel_intake", "test_model_configuration"]


@pytest.fixture(autouse=True)
def manual_ticks(monkeypatch):
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)


@pytest.mark.parametrize("analysis", ["clean", "cautions", "legacy_cautions"])
@pytest.mark.parametrize("mode", ["basic", "pi_skill"])
@pytest.mark.parametrize("trigger", ["automatic", "save_one", "save_separately"])
def test_wechat_capture_reaches_search_once_in_each_mode(
    config, entry, http, monkeypatch, vault_keychain, mode, trigger, analysis
):
    client, vault = entry[1].client_id, entry[1].vault_id
    grant_original(config, entry)
    layout = vault_save_contract.settings_wire(vault_save_contract.unified_settings())
    layout.pop("layout_mode")
    layout["image_dir"] = "Attachments/custom/images"
    activate(config, vault, **layout)
    with database.connect(config.state_root) as db:
        model = (
            catalog.save(
                config,
                db,
                Save(
                    provider="opencodego",
                    name="Synthetic",
                    model="glm-5.3-flash",
                    api_key="synthetic-key",
                ),
            )["id"]
            if mode == "pi_skill"
            else None
        )
        automatic_policy.save(
            config,
            db,
            vault,
            0,
            automatic_policy.Policy(
                policy_version=2,
                enabled=True,
                direct_to_inbox=True,
                client_id=client,
                processing_mode=mode,
                model_configuration_id=model,
                allow_model_source_content=mode == "pi_skill",
                allowed_kinds=["intake"],
            ),
        )
    enable(config, entry, policy_version=2, fetch_articles=True, store_limit=0, download_limit=0)
    batch = receive(config, entry, text=URL)
    if trigger != "automatic":
        response = http.post(f"/admin/batches/{batch}/choice", json={"action": trigger})
        assert response.status_code == 200, response.json()
    assert intake_processing.advance(config, entry[0]["id"]) == 1
    calls, _ = install(
        monkeypatch,
        [
            Reply(200, image_message_html(), [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
            Reply(200, PNG + b"second", [("Content-Type", "image/png")]),
        ],
    )
    executions = []

    def execute(config, policy, rid, prompt):
        executions.append(rid)
        with database.connect(config.state_root) as db:
            execution_ledger.start(config, db, rid, policy)
            call = execution_ledger.reserve(config, db, rid, policy)
            execution_ledger.settle(
                db, call, policy, {"prompt_tokens": 20, "completion_tokens": 10}
            )
            binding = pi_processing.get(db, rid)
            pi_processing.materialize(config, binding)
            data = json.loads(binding["input_json"])
            (config.state_root / "tasks" / rid / "draft.json").write_text(
                json.dumps(
                    {
                        "schema": "fiber-processing-draft/v1",
                        "kind": "article",
                        "source_sha256": binding["source_sha256"],
                        "summary": "Synthetic Pi evidence summary",
                        "evidence_ids": [data["evidence"][0]["id"]],
                        "gaps": []
                        if analysis == "clean"
                        else ["only one source fragment", "images not in text input"],
                    }
                )
            )
            execution_ledger.transition(db, rid, "succeeded")
        return {"state": "succeeded"}

    monkeypatch.setattr("fiber_bundle.execution.execute", execute)
    assert intake_extraction.advance(config, entry[0]["id"])
    if mode == "pi_skill":
        # Exercise compatibility of already selected legacy analysis separately
        # from the new capture path, which never requires this enrichment.
        from fiber_bundle.channels import intake_pi

        assert intake_pi.run(config, entry[0]["id"], batch, 0)
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert link["state"] == "review_required", link
    assert len(calls) == 3
    legacy = mode == "pi_skill" and analysis == "legacy_cautions"
    previous = None
    if mode == "pi_skill" and analysis != "clean":
        assert link["processing_gaps"] == []
        assert len(link["processing_cautions"]) == 2
    if legacy:
        from fiber_bundle.channels.intake_processing import record

        with database.locked(config), channel_store.opened(config) as side:
            row = side.execute("select * from channel_intake_records").fetchone()
            value = json.loads(row["value"])
            item = value["links"][0]
            item["pi_processing"].pop("analysis_contract")
            # The legacy capture predates the frozen archive retry identity as well.
            item["pi_processing"].pop("archive_event_id")
            item["processing_gaps"] = item.pop("processing_cautions")
            record(side, entry[0]["id"], batch, row["revision"], row["state"], row["reason"], value)
        automatic_ingestion.tick(config)
        with database.connect(config.state_root) as db:
            previous = dict(db.execute("select * from automatic_jobs").fetchone())
            assert previous["state"] == "needs_review"
            credential = tuple(db.execute("select * from automatic_reviews").fetchone())
            old_calls = [tuple(r) for r in db.execute("select * from model_calls")]
        with monkeypatch.context() as no_model:
            no_model.setattr(
                "fiber_bundle.execution.execute", lambda *a: pytest.fail("replayed model")
            )
            intake_extraction.retry(config, entry[0]["id"], batch, 0, row["revision"])
            assert intake_extraction.advance(config, entry[0]["id"])
        with database.connect(config.state_root) as db:
            assert (
                dict(
                    db.execute(
                        "select * from automatic_jobs where id=?", (previous["id"],)
                    ).fetchone()
                )
                == previous
            )
            assert tuple(db.execute("select * from automatic_reviews").fetchone()) == credential
            assert [tuple(r) for r in db.execute("select * from model_calls")] == old_calls
        packet = intake_reviews.packet(config, entry[0]["id"], batch, 0)
        assert "analysis.md" in packet["files"]
        with database.connect(config.state_root) as db, channel_store.readonly(config) as side:
            _, _, _, files = intake_reviews.load(config, db, side, entry[0]["id"], batch, 0)
            assert "分析局限" in files["analysis.md"].decode()
            assert b"only one source fragment" in files["analysis.md"]
    if mode == "pi_skill" and analysis == "cautions":
        # A completed v2 analysis may be retried before publication without another capture/run.
        with database.connect(config.state_root) as db:
            before_retry = db.execute("select count(*) from receipts").fetchone()[0]
        with monkeypatch.context() as no_model:
            no_model.setattr(
                "fiber_bundle.execution.execute", lambda *a: pytest.fail("replayed complete model")
            )
            intake_extraction.retry(
                config, entry[0]["id"], batch, 0, records(config, entry)[0]["revision"]
            )
            assert intake_extraction.advance(config, entry[0]["id"])
        with database.connect(config.state_root) as db:
            assert db.execute("select count(*) from receipts").fetchone()[0] == before_retry
    for _ in range(4):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        jobs = db.execute("SELECT state,reason FROM automatic_jobs").fetchall()
        assert sorted(r["state"] for r in jobs) == (
            ["complete", "needs_review"] if legacy else ["complete"]
        ), [dict(r) for r in jobs]
        hits = search.vault_search(config, db, client, "入口", [vault])["results"]
        assert len(hits) == 1
        assert hits[0]["note_path"] == "Inbox/合成贴图号.md"
        assert hits[0]["source_kind"] == "published_channel_content"
        location = db.execute("SELECT * FROM publication_locations").fetchone()
        assert location["entry_note_path"] == "Inbox/合成贴图号.md"
        assert location["package_root"] is None
        plan = json.loads(location["value"])
        assert plan["mapping"]["note.md"] == "Inbox/合成贴图号.md"
        root = config.vault_path("test vault/temp_vault1")
        original_text = (root / location["entry_note_path"]).read_text()

        def local_targets(text, current):
            targets = set()
            for begin, end, wiki, _, _ in publication_links.references(text):
                target = unquote(text[begin:end].split("|", 1)[0].split("#", 1)[0])
                targets.add(
                    target
                    if wiki
                    else posixpath.normpath(posixpath.join(posixpath.dirname(current), target))
                )
            return targets

        targets = local_targets(original_text, location["entry_note_path"])
        images = {
            logical: path for logical, path in plan["mapping"].items() if logical.endswith(".png")
        }
        assert len(images) == 2
        assert set(images.values()) <= targets
        assert all(path.startswith("Attachments/custom/images/") for path in images.values())
        assert {path.read_bytes() for path in (root / layout["image_dir"]).iterdir()} == {
            PNG,
            PNG + b"second",
        }
        analysis_hits = search.vault_search(
            config, db, client, "Synthetic Pi evidence summary", [vault]
        )["results"]
        if model:
            assert plan["mapping"]["analysis.md"] == "Inbox/合成贴图号-分析.md"
            assert set(plan["body_files"]) == {"note.md", "analysis.md"}
            assert plan["supplement_roles"] == {"analysis.md": "analysis"}
            analysis_path = plan["mapping"]["analysis.md"]
            assert analysis_path in targets
            assert location["entry_note_path"] in local_targets(
                (root / analysis_path).read_text(), analysis_path
            )
            assert len(analysis_hits) == 1
            assert analysis_hits[0]["note_path"] == analysis_path
            assert analysis_hits[0]["note_id"] != hits[0]["note_id"]
            assert analysis_hits[0]["source_kind"] == "agent_analysis"
            assert analysis_hits[0]["independent_evidence"] is False
            with channel_store.readonly(config) as side:
                _, _, _, files = intake_reviews.load(config, db, side, entry[0]["id"], batch, 0)
            assert json.loads(files["publication-notes.json"]) == {
                "schema": "fiber-note-supplements/v1",
                "supplements": [{"path": "analysis.md", "role": "analysis"}],
            }
        else:
            assert set(plan["body_files"]) == {"note.md"}
            assert "analysis.md" not in plan["mapping"]
            assert analysis_hits == []
        receipts = db.execute("SELECT count(*) FROM receipts").fetchone()[0]
        notes = db.execute("SELECT count(*) FROM publication_locations").fetchone()[0]
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == (1 if model else 0)
        if model:
            call = db.execute("SELECT reserved_nusd,charged_nusd FROM model_calls").fetchone()
            assert tuple(call) == (0, None)
            assert db.execute("SELECT charged FROM model_budget_reservations").fetchone()[0] == 30
        search_before = {"original": hits, "analysis": analysis_hits}
        files_before = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
    assert len(executions) == (1 if model else 0)
    assert note_search_build.build(config, client, vault)["state"] == "ready"
    with database.connect(config.state_root) as db:
        assert search.vault_search(config, db, client, "入口", [vault])["results"] == hits
        assert (
            search.vault_search(config, db, client, "Synthetic Pi evidence summary", [vault])[
                "results"
            ]
            == analysis_hits
        )
    with note_search_cache.connection(config, vault) as index:
        assert index is not None
        index_before = {
            "notes": [tuple(row) for row in index.execute("SELECT * FROM notes ORDER BY path")],
            "chunks": [
                tuple(row) for row in index.execute("SELECT * FROM chunks ORDER BY path,rowid")
            ],
        }
    # Refresh, repeated clicks and scheduling ticks cannot repeat a capture, model call or note.
    action = trigger if trigger != "automatic" else "save_one"
    assert http.post(f"/admin/batches/{batch}/choice", json={"action": action}).status_code == 200
    assert intake_processing.advance(config, entry[0]["id"]) == 0
    assert not intake_extraction.advance(config, entry[0]["id"])
    for _ in range(3):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == receipts
        assert db.execute("SELECT count(*) FROM publication_locations").fetchone()[0] == notes
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == (1 if model else 0)
        assert (
            search.vault_search(config, db, client, "入口", [vault])["results"]
            == search_before["original"]
        )
        assert (
            search.vault_search(config, db, client, "Synthetic Pi evidence summary", [vault])[
                "results"
            ]
            == search_before["analysis"]
        )
    assert {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    } == files_before
    with note_search_cache.connection(config, vault) as index:
        assert index is not None
        assert {
            "notes": [tuple(row) for row in index.execute("SELECT * FROM notes ORDER BY path")],
            "chunks": [
                tuple(row) for row in index.execute("SELECT * FROM chunks ORDER BY path,rowid")
            ],
        } == index_before
    progress = http.get("/admin/batches").json()[0]["intake"]
    assert progress["jobs"][0]["state"] == "complete"
    assert progress["links"][0]["published"] is True
