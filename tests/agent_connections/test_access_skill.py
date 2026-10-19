"""Quality-filtered transport and portable access material over real publications."""

import io
import zipfile

import pytest
import test_automatic_ingestion as ingestion

from fiber_bundle import automatic_ingestion, automatic_jobs, automatic_policy, database, search
from fiber_bundle.agent_connections import access_skill, application, configuration, files, store
from fiber_bundle.agent_connections.contracts import ConnectionSpec

pytest_plugins = ["test_source_imports", "test_execution"]


@pytest.fixture(autouse=True)
def explicit_tasks(monkeypatch):
    monkeypatch.setattr("fiber_bundle.automatic_ingestion.Runtime.start", lambda self: None)


def connect(config, db, vid, quality):
    spec = ConnectionSpec(
        schema_version="fiber-agent-connection/v2",
        name="Synthetic external reader",
        agent_type="mcp",
        vault_ids=[vid],
        quality_scope=quality,
    )
    item = configuration.create(config, db, spec, "create-" + vid + quality)
    application.apply(config, db, store.get(db, item["id"]), "http://127.0.0.1:18849")
    item = configuration.control(config, db, item["id"], "enable", 1, "enable-" + quality)
    return item


@pytest.mark.parametrize(
    "check,direct", [(True, False), (True, True), (False, False), (False, True)]
)
def test_real_four_switch_outputs_respect_connection_quality(
    config, owner, monkeypatch, check, direct
):
    original = ingestion.setup

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

    monkeypatch.setattr(ingestion, "setup", setup)
    cid, vid, item, job, rid = ingestion.admitted(config, owner)
    for _ in range(3):
        automatic_ingestion.tick(config)
    if not direct:
        with database.connect(config.state_root) as db:
            current = automatic_jobs.get(db, job["id"])
            assert current["state"] == "reviewed"
            automatic_jobs.settle(db, current, "queued", "publish")
        for _ in range(2):
            automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        all_reader = connect(config, db, vid, "all")
        strict_reader = connect(config, db, vid, "complete")
        all_results = search.vault_search(
            config, db, all_reader["client_id"], "verified-auto-token", [vid]
        )["results"]
        strict_results = search.vault_search(
            config, db, strict_reader["client_id"], "verified-auto-token", [vid]
        )["results"]
        assert len(all_results) == 1
        assert all_results[0]["completeness_status"] == ("passed" if check else "not_checked")
        assert len(strict_results) == (1 if check else 0)
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
        private = files.credential(config, all_reader["id"])
        material = access_skill.material(config, db, all_reader["id"])
        raw = access_skill.package(material)
        assert private.encode() not in raw
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            data = b"\n".join(archive.read(name) for name in archive.namelist())
            assert private.encode() not in data and str(config.project_root).encode() not in data
            assert b"verified-auto-token" not in data
            assert b"knowledge_verified=false" in data
        assert raw == access_skill.package(material)
        configuration.control(
            config, db, all_reader["id"], "pause", all_reader["revision"], "pause-reader"
        )
        from fiber_bundle import auth

        with pytest.raises(auth.Denied):
            search.vault_search(config, db, all_reader["client_id"], "verified-auto-token", [vid])


def test_legacy_configuration_serialization_and_digest_are_byte_compatible():
    from fiber_bundle.inputs import canonical
    from fiber_bundle.objects import digest

    from .support import context

    value = context()["configuration"]
    spec = ConnectionSpec.model_validate(value)
    assert spec.model_dump() == value
    assert spec.digest == digest(canonical(value).encode())
    with pytest.raises(ValueError):
        ConnectionSpec.model_validate({**value, "quality_scope": "complete"})


def test_access_download_and_preview_require_admin_and_csrf(http, config, store):
    with database.connect(config.state_root) as db:
        vid = db.execute("SELECT id FROM vaults LIMIT 1").fetchone()[0]
        item = connect(config, db, vid, "all")
    base = "/admin/agent-connections/" + item["id"]
    response = http.get(base + "/access-skill")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert (
        http.post(
            base + "/knowledge-preview",
            json={"query": "synthetic"},
            headers={"X-CSRF-Token": "wrong"},
        ).status_code
        == 403
    )
    assert (
        http.post(base + "/knowledge-preview", json={"query": "synthetic"}).json()["model_called"]
        is False
    )
    http.cookies.clear()
    assert http.get(base + "/access-skill").status_code == 401
