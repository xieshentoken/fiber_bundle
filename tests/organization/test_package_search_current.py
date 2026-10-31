import json

import pytest
from test_channel_natural_query import model_channel as model_channel
from test_message_channels import channel as channel
from test_model_configuration import vault_keychain as vault_keychain

from fiber_bundle import database, note_search_build, note_search_cache, search
from fiber_bundle.channels import natural_query, query, retrieval_evidence, store
from fiber_bundle.config import IntegrityError
from fiber_bundle.objects import digest
from fiber_bundle.organization import packages


def register(config, scope, package):
    cid, vid, _ = scope
    path = package / "references/runtime/catalog.json"
    value = json.loads(path.read_text())
    value["objects"] = [
        {"id": "ko-thermal", "title": "thermal", "statement": "thermal conduction draft evidence"}
    ]
    path.write_text(json.dumps(value))
    manifest = json.loads((package / "manifest.json").read_text())
    manifest["integrity"]["files"][packages.CATALOG] = digest(path.read_bytes())
    (package / "manifest.json").write_text(json.dumps(manifest))
    with database.connect(config.state_root) as db:
        return packages.register(config, db, cid, vid, "Book/fake-thermal")


@pytest.mark.parametrize("index", ["missing", "stale", "ready"])
def test_draft_and_base_evidence_survive_index_state(config, scope, package, index):
    cid, vid, root = scope
    register(config, scope, package)
    if index != "missing":
        note_search_build.build(config, cid, vid)
    (root / "base.md").write_text("# thermal\nthermal conduction base note evidence\n")
    if index == "ready":
        note_search_build.build(config, cid, vid)
    path = note_search_cache.location(config, vid)
    before = path.read_bytes() if path.exists() else None
    with database.connect(config.state_root) as db:
        response = search.vault_search(config, db, cid, "thermal", [vid], 2)
        assert len(response["results"]) == 2
        draft, note = response["results"]
        assert draft["expert_projection"] and note["note_path"] == "base.md"
        assert draft["artifact_stage"] == "draft" and draft["review_state"] == "deferred"
        assert draft["knowledge_verified"] is draft["independent_evidence"] is False
        assert packages.verify_evidence(config, db, cid, draft)["snippet"] == draft["snippet"]
    assert (path.read_bytes() if path.exists() else None) == before


def test_corrupt_draft_is_not_promoted_to_ordinary_evidence(config, scope, package):
    cid, vid, root = scope
    register(config, scope, package)
    note_search_build.build(config, cid, vid)
    (root / "base.md").write_text("# thermal\nthermal conduction current base note\n")
    (package / packages.CATALOG).write_text("{}")
    with (
        database.connect(config.state_root) as db,
        pytest.raises(IntegrityError, match="knowledge_module_changed"),
    ):
        search.vault_search(config, db, cid, "thermal", [vid])


@pytest.mark.parametrize("schema", ["channel-retrieval/v3", "channel-retrieval/v4"])
def test_later_query_draft_is_not_starved_by_first_query_base_notes(
    config, scope, package, model_channel, schema
):
    value, _ = model_channel
    cid, vid, root = scope
    register(config, scope, package)
    for i in range(5):
        (root / f"base{i}.md").write_text(f"# 基础材料{i}\n基础材料有编号{i}的独立文字记录。")
    with database.connect(config.state_root) as db, store.readonly(config) as side:
        current = store.get(side, value["id"])
        current["value"] = {**current["value"], "client_id": cid, "vault_id": vid}
        if schema == "channel-retrieval/v4":
            packet = natural_query.evidence(
                config, db, current, "综合基础材料与thermal", ["基础材料", "thermal"]
            )
        else:
            packet = retrieval_evidence.retrieve(
                config, db, current, "综合基础材料与thermal", ["基础材料", "thermal"], schema=schema
            )
        assert any(s.get("expert_projection") for s in packet["sources"])
        assert any(not s.get("expert_projection") for s in packet["sources"])
        assert len({s["note_path"] for s in packet["sources"]}) == 5
        assert packet["retrieval_recipe"]["schema"] == schema
        query.recheck(config, db, current, packet)
