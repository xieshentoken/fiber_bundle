from unittest.mock import patch

import pytest
from test_channel_natural_query import model_channel as model_channel
from test_message_channels import channel as channel
from test_model_configuration import vault_keychain as vault_keychain
from test_note_search_cache import indexed_scope as indexed_scope
from test_search import retrieve

from fiber_bundle import (
    auth,
    database,
    note_index,
    note_inventory,
    note_search_build,
    note_search_cache,
)
from fiber_bundle.channels import natural_query, query, store


@pytest.mark.parametrize("indexed", [False, True])
def test_multiple_terms_share_one_phase_and_recheck_fresh_sources(config, model_channel, indexed):
    value, root = model_channel
    cid, vid = value["settings"]["client_id"], value["settings"]["vault_id"]
    if indexed:
        note_search_build.build(config, cid, vid)
    (root / "new.md").write_text("# 青松\n青松原因来自热处理条件。\n")
    with database.connect(config.state_root) as db, store.readonly(config) as side:
        current = store.get(side, value["id"])
        with (
            patch.object(note_inventory, "read", wraps=note_inventory.read) as reads,
            patch.object(
                note_search_cache, "fingerprint", wraps=note_search_cache.fingerprint
            ) as fingerprints,
        ):
            packet = natural_query.evidence(
                config, db, current, "青松的原因", ["青松 缺失", "青松"]
            )
        assert reads.call_count == 1
        assert fingerprints.call_count == 2
        assert "new.md" in {x["note_path"] for x in packet["sources"]}
        query.recheck(config, db, current, packet)
        (root / "new.md").write_text("# 青松\n青松修改后的其他内容。\n")
        with pytest.raises(Exception, match="channel_source_changed"):
            query.recheck(config, db, current, packet)


@pytest.mark.parametrize("change", ["selected", "unrelated", "revoke"])
def test_change_during_current_query_is_rejected(http, config, indexed_scope, monkeypatch, change):
    cid, headers, vid, root = indexed_scope
    note_search_build.build(config, cid, vid)
    (root / "new.md").write_text("# newly added\nnew source\n")
    original = note_index.read_note
    seen = 0

    def changing(*args, **kwargs):
        nonlocal seen
        value = original(*args, **kwargs)
        if args[1] == "one.md":
            seen += 1
            if seen == 2:
                if change == "revoke":
                    with database.connect(config.state_root) as db:
                        auth.set_grant(db, cid, vid, query=False, original=False, add=False)
                else:
                    name = "one.md" if change == "selected" else "unrelated.md"
                    (root / name).write_text("# changed\nchanged during delivery\n")
        return value

    monkeypatch.setattr(note_index, "read_note", changing)
    response = retrieve(http, headers, "AB-12", [vid])
    assert response.status_code == (404 if change == "revoke" else 503)
    assert "snippet" not in response.text


def test_missing_index_current_read_keeps_per_note_safety(http, config, indexed_scope, monkeypatch):
    from fiber_bundle.config import IntegrityError

    _, headers, vid, _ = indexed_scope
    with patch.object(
        note_index, "read_note", side_effect=IntegrityError("cloud_item_not_downloaded")
    ):
        response = retrieve(http, headers, "青松", [vid])
    assert response.status_code == 503 and "snippet" not in response.text
    assert not note_search_cache.location(config, vid).exists()


def test_v2_recipe_remains_compatible(config, model_channel):
    value, _ = model_channel
    with database.connect(config.state_root) as db, store.readonly(config) as side:
        current = store.get(side, value["id"])
        packet = natural_query.evidence(config, db, current, "介绍青松", ["青松"])
        packet["retrieval_recipe"]["schema"] = "channel-retrieval/v2"
        packet["retrieval_recipe"].pop("preserved")
        query.recheck(config, db, current, packet)
