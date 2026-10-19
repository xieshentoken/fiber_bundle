"""Current-file delivery verifies new layout identities before and after persisted indexing."""

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_placement import published
from test_publication_layout import activate
from test_vault_layout_migration import legacy

from fiber_bundle import (
    auth,
    database,
    note_placement,
    note_placement_execution,
    note_search_build,
    publication_rebind,
    search,
    vault_layout_migration,
)
from fiber_bundle.objects import digest


@pytest.mark.parametrize("state", ["fresh", "archived", "converted", "rebound"])
def test_agent_retrieval_checks_actual_entry_and_stable_source(
    config, policy, article_scope, state
):
    if state == "converted":
        _, result, note = legacy(config, policy, article_scope)
        activate(config, article_scope[1])
        with database.connect(config.state_root) as db:
            p = vault_layout_migration.preview(
                config, db, *article_scope[:2], "article", result["publication_id"]
            )
            current = vault_layout_migration.apply(
                config, db, *article_scope[:2], p["operation_id"]
            )["target_path"]
    else:
        _, result, note = published(config, policy, article_scope)
        current = result["note_path"]
    with database.connect(config.state_root) as db:
        if state == "archived":
            p = note_placement.preview(
                config,
                db,
                *article_scope[:2],
                "publication:" + result["publication_id"],
                digest(note.read_bytes()),
            )
            current = note_placement_execution.apply(
                config, db, *article_scope[:2], p["operation_id"]
            )["target_path"]
        elif state == "rebound":
            target = note.parent / "Manual.md"
            note.rename(target)
            p = publication_rebind.preview(
                config,
                db,
                *article_scope[:2],
                "article",
                result["publication_id"],
                1,
                "Inbox/Manual.md",
            )
            current = publication_rebind.apply(config, db, *article_scope[:2], p["operation_id"])[
                "target_path"
            ]
        before = search.search(config, db, article_scope[0], "Synthetic", [article_scope[1]])[
            "results"
        ]
    note_search_build.build(config, *article_scope[:2])
    with database.connect(config.state_root) as db:
        after = search.search(config, db, article_scope[0], "Synthetic", [article_scope[1]])[
            "results"
        ]
        assert after == before and len(after) == 1
        assert (
            after[0]["note_path"] == current
            and after[0]["note_id"] == "publication:" + result["publication_id"]
        )
        assert after[0]["source_kind"] == "published_article" and after[0]["originals"]
        auth.set_grant(db, *article_scope[:2], query=True, original=False, add=True)
        assert (
            search.search(config, db, article_scope[0], "Synthetic", [article_scope[1]])["results"][
                0
            ]["originals"]
            == []
        )
