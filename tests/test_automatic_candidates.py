"""Real publisher/index paths with successful deterministic capture/processing fixtures."""

from test_article_publication import completed
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_media_jobs import media_scope as media_scope
from test_media_processing import complete
from test_publication_layout import activate

from fiber_bundle import auth, automatic_ingestion, automatic_policy, database, search
from fiber_bundle.vault_save_contract import settings_wire, unified_settings


def enable(config, cid, vid, kinds):
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=True, add=True, original=True)
    fields = settings_wire(unified_settings())
    fields.pop("layout_mode")
    activate(config, vid, **fields)
    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            vid,
            0,
            automatic_policy.Policy(
                client_id=cid,
                review_mode="automatic",
                publish_mode="automatic",
                allowed_kinds=kinds,
            ),
        )


def test_article_capture_automatically_publishes_with_real_actor(config, policy, article_scope):
    cid, vid = article_scope[:2]
    enable(config, cid, vid, ["article"])
    rid = completed(config, policy, article_scope)
    for _ in range(3):
        assert automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        assert not db.execute("SELECT 1 FROM article_reviews").fetchone()
        assert (
            db.execute("SELECT state FROM automatic_jobs WHERE source_id=?", (rid,)).fetchone()[0]
            == "complete"
        )
        notes = search.vault_search(config, db, cid, "synthetic", [vid])["results"]
        assert notes and all(n["review_actor"] == "system_rules" for n in notes)


def test_media_processing_publishes_without_human_audio_visual_claims(config, media_scope):
    cid, vid = media_scope[0]["id"], media_scope[1]
    enable(config, cid, vid, ["media"])
    job = complete(config, media_scope)
    for _ in range(3):
        assert automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        assert not db.execute("SELECT 1 FROM media_reviews").fetchone()
        row = db.execute("SELECT * FROM media_publications WHERE job_id=?", (job,)).fetchone()
        assert row["review_id"] is None and row["system_review_id"]
        notes = search.vault_search(config, db, cid, "Literal synthetic caption", [vid])["results"]
        assert notes and notes[0]["review_actor"] == "system_rules"
        root = config.project_root / "test vault/temp_vault1"
        note = root / notes[0]["note_path"]
        assert "reviewed by administrator" not in note.read_text()
