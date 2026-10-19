import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from test_media_jobs import create, fixture_worker
from test_media_jobs import media_scope as media_scope

from fiber_bundle import auth, media_asr_preflight, media_jobs, media_worker
from fiber_bundle import media_glossary as glossary
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.media_files import canonical
from fiber_bundle.media_glossary_contract import ambiguities, load

SNAPSHOT = {
    "schema": "fiber-media-glossary/v1",
    "topic": "Synthetic terminology",
    "terms": [
        {
            "canonical": "Fiber",
            "full_name": "Synthetic Fiber",
            "chinese_name": "合成术语",
            "preserve_case": True,
            "context_keywords": [],
            "aliases": [{"value": "fibre", "mode": "safe"}],
        }
    ],
    "unresolved": [],
}
RAW = json.dumps(SNAPSHOT, ensure_ascii=False, indent=2).encode()


def freeze(config, scope, job, raw=RAW):
    return glossary.freeze(config, scope[0]["id"], job, raw)


def confirm(config, scope, job, value):
    return glossary.confirm(
        config, scope[0]["id"], job, value["revision"], value["snapshot_sha256"]
    )


def test_raw_snapshot_confirmation_and_no_automatic_approval(config, media_scope):
    cid = media_scope[0]["id"]
    job = create(config, media_scope)
    assert glossary.status(config, cid, job)["blocking_reasons"] == ["glossary_missing"]
    frozen = freeze(config, media_scope, job)
    assert frozen["revision"] == 1 and not frozen["confirmation_recorded"]
    assert not frozen["terminology_gate_passed"] and "Synthetic" not in str(frozen)
    approved = confirm(config, media_scope, job, frozen)
    assert approved["terminology_gate_passed"] and approved["confirmation_recorded"]
    assert not any(
        approved[k]
        for k in (
            "asr_ready",
            "asr_started",
            "publication_ready",
            "cleanup_allowed",
            "semantic_review_verified",
        )
    )
    assert confirm(config, media_scope, job, frozen) == approved
    assert freeze(config, media_scope, job, canonical(SNAPSHOT)) == approved
    with media_jobs.connection(config) as db:
        row = glossary.latest(db, job)
        assert row["raw_json"].encode() == RAW
        assert glossary.verified(row) == SNAPSHOT
        assert db.execute("SELECT count(*) FROM media_glossary_confirmations").fetchone()[0] == 1


def test_change_and_aba_never_reuse_old_confirmation(config, media_scope):
    job = create(config, media_scope)
    first = freeze(config, media_scope, job)
    confirm(config, media_scope, job, first)
    changed = {**SNAPSHOT, "topic": "Changed synthetic topic"}
    second = freeze(config, media_scope, job, canonical(changed))
    assert second["revision"] == 2 and not second["confirmation_recorded"]
    with pytest.raises(InvalidInput, match="stale"):
        confirm(config, media_scope, job, first)
    third = freeze(config, media_scope, job)
    assert third["revision"] == 3 and third["snapshot_sha256"] == first["snapshot_sha256"]
    assert not third["confirmation_recorded"]
    with pytest.raises(InvalidInput, match="stale"):
        confirm(config, media_scope, job, first)
    assert confirm(config, media_scope, job, third)["terminology_gate_passed"]
    with media_jobs.connection(config) as db:
        assert (
            db.execute(
                "SELECT revision FROM media_glossary_confirmations ORDER BY revision"
            ).fetchall()[0][0]
            == 1
        )
        assert db.execute("SELECT count(*) FROM media_glossary_revisions").fetchone()[0] == 3


def test_revision_limit_preserves_last_confirmed_snapshot(config, media_scope):
    job = create(config, media_scope)
    for revision in range(1, 101):
        current = freeze(config, media_scope, job, canonical({**SNAPSHOT, "topic": str(revision)}))
    approved = confirm(config, media_scope, job, current)
    with pytest.raises(InvalidInput, match="revision_limit"):
        freeze(config, media_scope, job, canonical({**SNAPSHOT, "topic": "101"}))
    assert glossary.status(config, media_scope[0]["id"], job) == approved
    assert freeze(config, media_scope, job, canonical({**SNAPSHOT, "topic": "100"})) == approved


def test_revocation_before_confirmation_preserves_snapshot_without_approval(config, media_scope):
    job = create(config, media_scope)
    frozen = freeze(config, media_scope, job)
    with media_jobs.connection(config) as db:
        auth.set_grant(
            db, media_scope[0]["id"], media_scope[1], query=False, original=False, add=False
        )
    with pytest.raises(auth.Denied):
        confirm(config, media_scope, job, frozen)
    with media_jobs.connection(config) as db:
        assert glossary.verified(glossary.latest(db, job)) == SNAPSHOT
        assert db.execute("SELECT count(*) FROM media_glossary_confirmations").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["shared_alias", "canonical_alias", "context_missing", "manual"])
def test_ambiguities_are_persisted_and_cannot_be_confirmed(config, media_scope, kind):
    snapshot = copy.deepcopy(SNAPSHOT)
    if kind in {"shared_alias", "canonical_alias"}:
        other = copy.deepcopy(snapshot["terms"][0])
        other["canonical"] = "Other"
        other["aliases"][0]["value"] = "fibre" if kind == "shared_alias" else "FIBER"
        snapshot["terms"].append(other)
    elif kind == "context_missing":
        snapshot["terms"][0]["aliases"][0]["mode"] = "contextual"
    else:
        snapshot["unresolved"] = [{"term": "unknown", "reason": "User review needed"}]
    job = create(config, media_scope)
    frozen = freeze(config, media_scope, job, canonical(snapshot))
    assert frozen["unresolved_count"] > 0 and "glossary_ambiguous" in frozen["blocking_reasons"]
    with pytest.raises(InvalidInput, match="ambiguous"):
        confirm(config, media_scope, job, frozen)
    assert ambiguities(load(canonical(snapshot)))


def test_explicit_empty_confirmation_is_required(config, media_scope):
    job = create(config, media_scope)
    frozen = freeze(config, media_scope, job, canonical({**SNAPSHOT, "terms": []}))
    with pytest.raises(InvalidInput, match="empty_review"):
        confirm(config, media_scope, job, frozen)
    assert glossary.confirm(
        config,
        media_scope[0]["id"],
        job,
        frozen["revision"],
        frozen["snapshot_sha256"],
        allow_empty=True,
    )["terminology_gate_passed"]


@pytest.mark.parametrize(
    "query,download,add",
    [(q, o, a) for q in (False, True) for o in (False, True) for a in (False, True)],
)
def test_independent_grants(config, media_scope, query, download, add):
    cid, vault = media_scope[0]["id"], media_scope[1]
    job = create(config, media_scope)
    with media_jobs.connection(config) as db:
        auth.set_grant(db, cid, vault, query=query, original=download, add=add)
    if add:
        value = freeze(config, media_scope, job)
        confirm(config, media_scope, job, value)
    else:
        with pytest.raises(auth.Denied):
            freeze(config, media_scope, job)
        with pytest.raises(auth.Denied):
            glossary.status(config, cid, job)
    if query:
        assert glossary.show(config, cid, job)["snapshot"] == (SNAPSHOT if add else None)
    else:
        with pytest.raises(auth.Denied):
            glossary.show(config, cid, job)


def test_other_owner_cannot_share_task_or_confirmation(config, media_scope):
    job = create(config, media_scope)
    frozen = freeze(config, media_scope, job)
    with media_jobs.connection(config) as db:
        other = auth.create_client(db, "other-glossary-owner", False)
        auth.set_grant(db, other["id"], media_scope[1], add=True, query=True, original=True)
    with pytest.raises(auth.Denied):
        glossary.freeze(config, other["id"], job, RAW)
    with pytest.raises(auth.Denied):
        glossary.show(config, other["id"], job)
    with pytest.raises(auth.Denied):
        glossary.confirm(config, other["id"], job, frozen["revision"], frozen["snapshot_sha256"])


@pytest.mark.parametrize("state", ["cancelled", "interrupted", "running", "failed"])
def test_stopped_or_unknown_job_blocks_confirmation_and_asr(config, media_scope, state):
    cid = media_scope[0]["id"]
    job = create(config, media_scope)
    frozen = freeze(config, media_scope, job)
    confirm(config, media_scope, job, frozen)
    with media_jobs.connection(config) as db:
        db.execute("UPDATE media_tasks SET state=? WHERE id=?", (state, job))
    assert not glossary.status(config, cid, job)["terminology_gate_passed"]
    with pytest.raises(InvalidInput):
        freeze(config, media_scope, job)
    with pytest.raises(InvalidInput):
        confirm(config, media_scope, job, frozen)
    with patch("fiber_bundle.media_asr_capabilities.inspect") as probe:
        assert (
            media_asr_preflight.inspect(config, cid, job)["dependency_probe"]["state"] == "NOT_RUN"
        )
        probe.assert_not_called()


@pytest.mark.parametrize("change", ["raw", "snapshot", "confirmation", "owner"])
def test_tampered_persisted_snapshot_or_confirmation_rejected(config, media_scope, change):
    cid = media_scope[0]["id"]
    job = create(config, media_scope)
    frozen = freeze(config, media_scope, job)
    confirm(config, media_scope, job, frozen)
    with media_jobs.connection(config) as db:
        if change == "raw":
            db.execute(
                "UPDATE media_glossary_revisions SET raw_json=? WHERE job_id=?",
                (canonical({**SNAPSHOT, "topic": "changed"}).decode(), job),
            )
        elif change == "snapshot":
            db.execute(
                "UPDATE media_glossary_revisions SET snapshot_sha256=? WHERE job_id=?",
                ("0" * 64, job),
            )
        elif change == "confirmation":
            db.execute(
                "UPDATE media_glossary_confirmations SET snapshot_sha256=? WHERE job_id=?",
                ("0" * 64, job),
            )
        else:
            other = auth.create_client(db, "tampered-owner", False)
            db.execute(
                "UPDATE media_glossary_confirmations SET client_id=? WHERE job_id=?",
                (other["id"], job),
            )
    with pytest.raises(IntegrityError):
        glossary.status(config, cid, job)


def test_completed_subtitle_candidate_is_never_corrected_or_reexecuted(config, media_scope):
    cid = media_scope[0]["id"]
    job = create(config, media_scope)
    with patch.object(media_worker, "execute", side_effect=fixture_worker):
        media_jobs.run(config, cid, job)
    with media_jobs.connection(config) as db:
        auth.set_grant(db, cid, media_scope[1], add=True, query=True, original=False)
    before = media_jobs.result(config, cid, job)
    inputs = media_worker.directory(config, job) / "input"
    outputs = inputs.parent / "output"
    original = {str(p): p.read_bytes() for d in (inputs, outputs) for p in d.iterdir()}
    value = freeze(config, media_scope, job)
    confirm(config, media_scope, job, value)
    assert media_jobs.result(config, cid, job) == before and before["subtitle"]["corrections"] == []
    assert all(p.read_bytes() == raw for name, raw in original.items() for p in [Path(name)])


@pytest.mark.parametrize(
    "kind", ["extra", "duplicate", "nan", "unicode", "type", "oversize", "canonical_duplicate"]
)
def test_strict_glossary_input(kind):
    value = copy.deepcopy(SNAPSHOT)
    if kind == "extra":
        value["confirmation"] = True
    elif kind == "unicode":
        value["topic"] = "\ud800"
    elif kind == "type":
        value["terms"][0]["preserve_case"] = 1
    elif kind == "canonical_duplicate":
        value["terms"].append(copy.deepcopy(value["terms"][0]))
    raw = json.dumps(value).encode()
    if kind == "duplicate":
        raw = raw.replace(b'"topic":', b'"topic":"duplicate","topic":')
    elif kind == "nan":
        raw = raw.replace(b"true", b"NaN")
    elif kind == "oversize":
        raw = b" " * 65537
    with pytest.raises(InvalidInput):
        load(raw)
