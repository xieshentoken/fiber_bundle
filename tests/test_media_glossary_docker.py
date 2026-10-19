"""Opt-in dependency metadata probe after synthetic confirmation; no ASR or real media."""

import hashlib

from test_media_docker import create
from test_media_docker import docker_scope as docker_scope
from test_media_glossary import RAW

from fiber_bundle import media_asr_preflight, media_glossary, media_jobs


def test_real_dependency_probe_is_blocked_and_preserves_source_and_ledgers(config, docker_scope):
    job, data = create(config, docker_scope, raw=None)
    cid = docker_scope[0]["id"]
    with media_jobs.connection(config) as db:
        before = dict(media_jobs.load(db, cid, job))
        bound = media_jobs.binding(media_jobs.load(db, cid, job))
    frozen = media_glossary.freeze(config, cid, job, RAW)
    media_glossary.confirm(config, cid, job, frozen["revision"], frozen["snapshot_sha256"])
    result = media_asr_preflight.inspect(config, cid, job)
    assert result["state"] == "BLOCKED" and not result["asr_ready"]
    assert result["terminology"]["terminology_gate_passed"]
    probe = result["dependency_probe"]
    assert probe["state"] == "INSPECTED"
    assert probe["media_mounts"] == probe["model_imports"] == probe["model_calls"] == 0
    assert not probe["weights_downloaded"] and not probe["runtime_qualified"]
    assert probe["resource_acceptance"] == "NOT_RUN"
    with media_jobs.connection(config) as db:
        assert dict(media_jobs.load(db, cid, job)) == before
        for table in ("model_calls", "execution_runs", "document_tasks"):
            assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    assert bound["source"]["sha256"] == hashlib.sha256(data).hexdigest()
