"""A configured VL1 draft must not silently activate unfinished publishers."""

from test_article_publication import completed, destination
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy

from fiber_bundle import article_publication, database, vault_save_policy
from fiber_bundle.vault_save_contract import SaveSettings


def test_draft_policy_preserves_legacy_article_publication(config, policy, article_scope):
    with database.connect(config.state_root) as db:
        preview = vault_save_policy.preview(
            config, db, article_scope[1], SaveSettings(layout_mode="obsidian_folders"), 0
        )
        saved = vault_save_policy.save(config, db, article_scope[1], preview["preview_id"], 0)
        assert saved["publication_ready"] is False
    run_id = completed(config, policy, article_scope)
    result = article_publication.publish(config, article_scope[0], run_id)
    note = destination(config, result)
    assert note.name == "note.md" and note.parent.name.startswith("article-")
    assert (note.parent / "images").is_dir()
    assert not list((note.parent.parent / "Inbox").iterdir())
    assert article_publication.publish(config, article_scope[0], run_id) == result
