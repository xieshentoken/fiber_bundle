import copy
import json
import subprocess
from unittest.mock import patch

import pytest
from test_media_glossary import RAW, SNAPSHOT, confirm, freeze
from test_media_jobs import POLICY, create
from test_media_jobs import media_scope as media_scope

from fiber_bundle import auth, media_asr_preflight, media_glossary, media_jobs
from fiber_bundle import media_asr_capabilities as capabilities
from fiber_bundle.config import InvalidInput
from fiber_bundle.media_files import canonical

METADATA = {
    "schema": "fiber-asr-dependencies/v1",
    "python_version": "3.13.12",
    "packages": {
        p: {"state": "MISSING", "version": None, "metadata_sha256": None}
        for p in capabilities.PACKAGES
    },
}


def ready(config, scope):
    job = create(config, scope, subtitle_receipt_id=None, subtitle_asset_id=None)
    value = freeze(config, scope, job)
    confirm(config, scope, job, value)
    return job


def test_confirmation_is_required_before_any_dependency_probe(config, media_scope):
    job = create(config, media_scope)
    with patch.object(capabilities, "inspect") as probe:
        value = media_asr_preflight.inspect(config, media_scope[0]["id"], job)
        assert value["dependency_probe"]["state"] == "NOT_RUN" and not value["asr_ready"]
        probe.assert_not_called()
    freeze(config, media_scope, job)
    with patch.object(capabilities, "inspect") as probe:
        assert (
            "glossary_confirmation_required"
            in media_asr_preflight.inspect(config, media_scope[0]["id"], job)["blocking_reasons"]
        )
        probe.assert_not_called()


@pytest.mark.parametrize("installed", [False, True])
def test_dependency_presence_never_grants_asr_or_semantic_approval(config, media_scope, installed):
    job = ready(config, media_scope)
    metadata = copy.deepcopy(METADATA)
    if installed:
        for item in metadata["packages"].values():
            item.update(state="FOUND", version="1.0.0", metadata_sha256="a" * 64)
    with patch.object(
        capabilities, "inspect", return_value={"state": "INSPECTED", "metadata": metadata}
    ):
        value = media_asr_preflight.inspect(config, media_scope[0]["id"], job)
    assert value["terminology"]["terminology_gate_passed"] and value["state"] == "BLOCKED"
    assert not value["asr_ready"] and not value["asr_started"] and value["model_calls"] == 0
    assert not value["license_verified"] and not value["model_selection_verified"]
    assert "asr_resource_acceptance_not_run" in value["blocking_reasons"]


@pytest.mark.parametrize("change", ["revision", "cancel", "revoke", "source"])
def test_preflight_rechecks_after_probe_before_return(config, media_scope, change):
    cid = media_scope[0]["id"]
    job = ready(config, media_scope)

    def probe(image):
        if change == "revision":
            media_glossary.freeze(config, cid, job, canonical({**SNAPSHOT, "topic": "new"}))
        elif change == "cancel":
            media_jobs.stop(config, cid, job)
        elif change == "revoke":
            with media_jobs.connection(config) as db:
                auth.set_grant(db, cid, media_scope[1], add=False, query=False, original=False)
        else:
            with media_jobs.connection(config) as db:
                db.execute(
                    "UPDATE receipts SET state='failed' WHERE job_id=?", (media_scope[2][0][0],)
                )
        return {"state": "INSPECTED", "metadata": METADATA}

    with patch.object(capabilities, "inspect", side_effect=probe):
        with pytest.raises((InvalidInput, auth.Denied)):
            media_asr_preflight.inspect(config, cid, job)


def test_existing_subtitle_is_retained_without_reexecution(config, media_scope):
    job = create(config, media_scope)
    value = freeze(config, media_scope, job)
    confirm(config, media_scope, job, value)
    with patch.object(
        capabilities, "inspect", return_value={"state": "INSPECTED", "metadata": METADATA}
    ):
        assert (
            "existing_subtitle_retained"
            in media_asr_preflight.inspect(config, media_scope[0]["id"], job)["blocking_reasons"]
        )
    with media_jobs.connection(config) as db:
        assert (
            db.execute("SELECT state FROM media_tasks WHERE id=?", (job,)).fetchone()[0] == "queued"
        )
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


def test_metadata_probe_has_no_private_mounts_network_or_model_imports(monkeypatch):
    calls = []

    def run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(METADATA).encode())

    monkeypatch.setattr(capabilities.subprocess, "run", run)
    value = capabilities.inspect(POLICY.image)
    assert value["state"] == "INSPECTED" and not value["runtime_qualified"]
    create, start, stop = calls
    assert "--mount" not in create and "--env" not in create and "--publish" not in create
    assert create[create.index("--network") + 1] == "none"
    assert create[create.index("--pull") + 1] == "never"
    assert "--read-only" in create and create[create.index("--user") + 1] == "1000:1000"
    assert create[-3:] == ["-I", "-c", capabilities.PROBE] and "qwen_asr" not in capabilities.PROBE
    assert RAW.decode() not in " ".join(create) and "Synthetic terminology" not in " ".join(create)
    assert start[-1] == stop[-1]


@pytest.mark.parametrize("kind", ["missing", "timeout", "bad_output", "stop_failure", "oversize"])
def test_probe_failures_are_bounded_and_never_ready(monkeypatch, kind):
    calls = []

    def run(args, **kw):
        calls.append(args)
        if kind == "missing" and args[1] == "create":
            raise OSError("private sentinel")
        if kind == "timeout" and args[1] == "start":
            raise subprocess.TimeoutExpired("private sentinel", 15)
        if kind == "stop_failure" and args[1] == "rm":
            raise OSError("private sentinel")
        raw = json.dumps(METADATA).encode()
        if kind == "bad_output":
            raw = b"{}"
        if kind == "oversize":
            raw = b" " * 8193
        return subprocess.CompletedProcess(args, 0, stdout=raw)

    monkeypatch.setattr(capabilities.subprocess, "run", run)
    value = capabilities.inspect(POLICY.image)
    assert value["state"] == "BLOCKED" and "private sentinel" not in str(value)
    assert not value["runtime_qualified"] and value["model_imports"] == 0
    assert len(calls) == (1 if kind == "missing" else 3)


@pytest.mark.parametrize("change", ["extra", "version", "hash", "package", "state"])
def test_untrusted_metadata_fields_rejected(change):
    value = copy.deepcopy(METADATA)
    if change == "extra":
        value["cookie"] = "private sentinel"
    elif change == "package":
        value["packages"]["unknown"] = {}
    elif change == "state":
        value["packages"]["torch"]["state"] = []
    else:
        value["packages"]["torch"].update(state="FOUND", version="1.0.0", metadata_sha256="a" * 64)
        value["packages"]["torch"]["version" if change == "version" else "metadata_sha256"] = (
            "private sentinel"
        )
    with pytest.raises(ValueError):
        capabilities.validate(value)
