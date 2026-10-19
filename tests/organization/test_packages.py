import json

import pytest

from fiber_bundle import auth, database, expert_query, published_expert
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import packages, store


def test_register_repeat_and_lazy_text(config, scope, package, monkeypatch):
    cid, vid, root = scope
    before = {str(p): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    with database.connect(config.state_root) as db:
        first = packages.register(config, db, cid, vid, "Book/fake-thermal")
        assert first == packages.register(config, db, cid, vid, "Book/fake-thermal")
        reads = []
        original = packages.read

        def track(root, path):
            reads.append(path)
            return original(root, path)

        monkeypatch.setattr(packages, "read", track)
        module = published_expert.text(config, db, cid, first["id"], first["version"])
        assert not any(p.endswith(".png") or p.endswith(".py") for p in reads)
        assert module["knowledge_verified"] is False and module["host_reference_adapter"]
        result = expert_query.execute(module, "thermal", 5)
        assert result["behavior"] == "abstain"
    assert before == {str(p): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    assert len(store.rows(config, "packages", vid)) == 1


def test_member_tamper_and_revocation(config, scope, package):
    cid, vid, _ = scope
    with database.connect(config.state_root) as db:
        first = packages.register(config, db, cid, vid, "Book/fake-thermal")
        (package / "assets/test.png").write_bytes(b"changed")
        with pytest.raises(IntegrityError):
            published_expert.member(
                config, db, cid, first["id"], first["version"], "assets/test.png"
            )
        auth.set_grant(db, cid, vid, query=False, original=False, add=False)
        with pytest.raises(auth.Denied):
            published_expert.text(config, db, cid, first["id"])


def test_symlink_and_scope(config, scope, package):
    cid, vid, _ = scope
    (package / "assets/test.png").unlink()
    (package / "assets/test.png").symlink_to("/etc/hosts")
    with database.connect(config.state_root) as db, pytest.raises((InvalidInput, IntegrityError)):
        packages.register(config, db, cid, vid, "Book/fake-thermal")


def test_new_version_keeps_old_text_snapshot(config, scope, package):
    cid, vid, _ = scope
    with database.connect(config.state_root) as db:
        first = packages.register(config, db, cid, vid, "Book/fake-thermal")
        m = json.loads((package / "manifest.json").read_text())
        m["package"]["version"] = "2"
        (package / "manifest.json").write_text(json.dumps(m))
        second = packages.register(config, db, cid, vid, "Book/fake-thermal")
        assert first["id"] == second["id"] and first["version"] != second["version"]
        old = published_expert.text(config, db, cid, first["id"], first["version"])
        assert old["version"] == first["version"]
        assert json.loads(old["files"]["manifest.json"])["package"]["version"] == "1"


def test_external_search_and_agent_reverification(config, scope, package):
    from agent_connections.test_access_skill import connect

    from fiber_bundle import search
    from fiber_bundle.agent_connections import evidence, evidence_verification
    from fiber_bundle.agent_connections import store as connections
    from fiber_bundle.objects import digest

    cid, vid, _ = scope
    path = package / "references/runtime/catalog.json"
    value = json.loads(path.read_text())
    value["objects"] = [
        {
            "id": "ko-thermal",
            "kind": "object",
            "title": "thermal",
            "statement": "thermal conduction",
            "terms": {"thermal": 2.0, "conduction": 1.0},
            "length": 3.0,
            "source_ids": [],
            "evidence_ids": [],
            "physical_pages": [],
            "printed_page_labels": [],
            "figure_refs": [],
        }
    ]
    path.write_text(json.dumps(value))
    manifest = json.loads((package / "manifest.json").read_text())
    manifest["integrity"]["files"]["references/runtime/catalog.json"] = digest(path.read_bytes())
    (package / "manifest.json").write_text(json.dumps(manifest))
    with database.connect(config.state_root) as db:
        packages.register(config, db, cid, vid, "Book/fake-thermal")
        reader = connect(config, db, vid, "all")
        client = reader["client_id"]
        result = search.vault_search(config, db, client, "thermal", [vid])
        projected = evidence.project(result, (vid,), 10)
        assert projected[0]["expert_projection"] is True
        context = connections.context(config, db, reader["id"])
        assert evidence_verification.verify(config, db, client, context, projected) == {
            "verified": True
        }
        changed = [{**projected[0], "snippet": "injected"}]
        with pytest.raises((InvalidInput, connections.Conflict)):
            evidence_verification.verify(config, db, client, context, changed)
