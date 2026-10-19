"""Four switch combinations execute the real extraction/publication/index path."""

import json

import pytest
import test_automatic_ingestion as fixture

from fiber_bundle import automatic_ingestion, automatic_jobs, automatic_policy, database, search

pytest_plugins = ["test_source_imports"]


@pytest.fixture(autouse=True)
def explicit_ticks(monkeypatch):
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)


@pytest.mark.parametrize(
    "check,direct", [(True, False), (True, True), (False, False), (False, True)]
)
def test_independent_switches_publish_truthfully(config, store, owner, monkeypatch, check, direct):
    original = fixture.setup

    def setup(config, owner):
        cid, vid = original(config, owner)
        with database.connect(config.state_root) as db:
            automatic_policy.save(
                config,
                db,
                vid,
                1,
                automatic_policy.Policy(
                    policy_version=2,
                    enabled=True,
                    client_id=cid,
                    completeness_check=check,
                    direct_to_inbox=direct,
                    allowed_kinds=["document"],
                ),
            )
        return cid, vid

    monkeypatch.setattr(fixture, "setup", setup)
    cid, vid, item, job, rid = fixture.admitted(config, owner)
    for _ in range(3):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        final = automatic_jobs.get(db, job["id"])
        assert final["state"] == ("complete" if direct else "reviewed")
        value = json.loads(db.execute("SELECT value FROM automatic_reviews").fetchone()[0])
        assert value["completeness_status"] == ("passed" if check else "not_checked")
        assert value["decision"] == ("accept" if check else "save_unchecked")
        assert db.execute("SELECT count(*) FROM document_reviews").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
        if direct:
            hit = search.vault_search(config, db, cid, "verified-auto-token", [vid])["results"][0]
            assert hit["completeness_status"] == ("passed" if check else "not_checked")
            if not check:
                assert "未进行内容完整性" in hit["source_label"]
        else:
            assert db.execute("SELECT count(*) FROM document_publications").fetchone()[0] == 0
