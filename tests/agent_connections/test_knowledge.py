"""Current connection grants over real notes and real compiler-published Drafts."""

import json
import os

import pytest
from test_document_evidence import completed as completed
from test_document_evidence import document_scope as document_scope

from fiber_bundle import auth, database, published_expert
from fiber_bundle.agent_connections import configuration, knowledge
from fiber_bundle.config import IntegrityError, InvalidInput

from .test_access_skill import connect

pytest_plugins = ["test_source_imports", "test_execution"]


@pytest.fixture(autouse=True)
def explicit_tasks(monkeypatch):
    monkeypatch.setattr("fiber_bundle.automatic_ingestion.Runtime.start", lambda self: None)


def test_note_version_time_scope_and_pause(config, owner):
    _, _, ids = owner
    root = config.project_root / "test vault/temp_vault1"
    path = root / "timeline.md"
    path.write_text("# Timeline\n[0–1000 ms] first\n[1000–2000 ms] second\n")
    with database.connect(config.state_root) as db:
        reader = connect(config, db, ids["personal"], "all")
        other = connect(config, db, ids["supplier"], "all")
        packet = knowledge.catalog(config, db, reader["client_id"], None)
        module = next(i for i in packet["items"] if i["title"] == "Timeline")
        request = knowledge.Read(
            module_id=module["module_id"],
            expected_version=module["version"],
            time_start_ms=1000,
            time_end_ms=2000,
        )
        result = knowledge.read_evidence(config, db, reader["client_id"], request)
        assert result["text"] == "[1000–2000 ms] second"
        assert result["completeness_status"] == "unknown" and not result["model_called"]
        with pytest.raises(auth.Denied):
            knowledge.read_evidence(config, db, other["client_id"], request)
        with pytest.raises(InvalidInput, match="locator"):
            knowledge.read_evidence(
                config, db, reader["client_id"], request.model_copy(update={"physical_page": 1})
            )
        path.write_text("# Timeline\nchanged\n")
        with pytest.raises(InvalidInput, match="stale"):
            knowledge.read_evidence(config, db, reader["client_id"], request)
        configuration.control(config, db, reader["id"], "pause", reader["revision"], "pause")
        with pytest.raises(auth.Denied):
            knowledge.catalog(config, db, reader["client_id"], None)


def test_published_native_page_locator_matches_current_bytes(config, document_scope, completed):
    from test_document_human_publication import approved

    from fiber_bundle import document_publication

    db = approved(config, document_scope, completed)
    document_publication.publish_locked(config, db, completed)
    reader = connect(config, db, document_scope[1], "all")
    module = next(
        m
        for m in knowledge.catalog(config, db, reader["client_id"], None)["items"]
        if m.get("source_kind") == "published_document"
    )
    request = knowledge.Read(
        module_id=module["module_id"],
        expected_version=module["version"],
        physical_page=1,
        end_line=200,
    )
    value = knowledge.read_evidence(config, db, reader["client_id"], request)
    assert "Measured" in value["text"] and value["lines"]
    with pytest.raises(InvalidInput, match="locator"):
        knowledge.read_evidence(
            config, db, reader["client_id"], request.model_copy(update={"physical_page": 3})
        )
    db.close()


@pytest.mark.skipif(
    not os.getenv("FIBER_EXPERT_RUNTIME"), reason="installed fixed compiler required"
)
def test_published_expert_uses_reader_grants_and_original_runtime(
    config, owner, policy, monkeypatch
):
    from test_expert_authoring import test_real_native_compiler_pi_proposals_and_draft_end_to_end

    test_real_native_compiler_pi_proposals_and_draft_end_to_end(
        config, owner, policy, monkeypatch, True, True, False
    )
    owner_id, _, ids = owner
    with database.connect(config.state_root) as db:
        task = db.execute(
            "SELECT task_id FROM source_expert_publications WHERE state='published'"
        ).fetchone()[0]
        reader = connect(config, db, ids["personal"], "all")
        other = connect(config, db, ids["supplier"], "all")
        module = published_expert.load(config, db, reader["client_id"], task)
        request = knowledge.Read(
            module_id=module["module_id"],
            expected_version=module["version"],
            kind="object",
            identity=module["objects"][0]["id"],
            physical_page=1,
        )
        result = knowledge.read_evidence(config, db, reader["client_id"], request)
        assert result["review_state"] == "deferred" and not result["knowledge_verified"]
        assert not result["independent_evidence"]
        with pytest.raises(auth.Denied):
            published_expert.load(config, db, other["client_id"], task)
        with pytest.raises(InvalidInput, match="stale"):
            published_expert.load(config, db, reader["client_id"], task, "f" * 64)
        with pytest.raises(auth.Denied):
            published_expert.load(config, db, reader["client_id"], "guessed-unpublished")
        with pytest.raises(auth.Denied):
            knowledge.read_evidence(
                config,
                db,
                reader["client_id"],
                request.model_copy(
                    update={
                        "kind": "attachment",
                        "identity": "manifest.json",
                        "physical_page": None,
                    }
                ),
            )
        # Prove this path does not borrow the processing owner's current grants.
        db.execute("UPDATE clients SET active=0 WHERE id=?", (owner_id,))
        db.commit()
    query = knowledge.query_expert(
        config, reader["client_id"], module["module_id"], module["version"], "Synthetic", 5
    )
    assert query["model_called"] is False and query["review_state"] == "deferred"
    assert query["query_result"]["evidence_ids"]
    assert set(query["query_result"]["evidence_ids"]) <= {a["id"] for a in module["anchors"]}
    assert query["query_result"]["assurance"]["knowledge_verified"] is False
    with database.connect(config.state_root) as db:
        location = db.execute(
            "SELECT value FROM publication_locations WHERE publication_id=?", (task,)
        ).fetchone()[0]
        mapping = json.loads(location)["mapping"]
        relative = mapping[".fiber-expert-canonical/manifest.json"]
        (config.project_root / "test vault/temp_vault1" / relative).write_bytes(b"changed")
        with pytest.raises(IntegrityError, match="changed"):
            published_expert.load(config, db, reader["client_id"], task)
