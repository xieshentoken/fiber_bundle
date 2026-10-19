import base64
import hashlib
from unittest.mock import patch

import pytest
from test_media_candidates import PROBE, RAW

from fiber_bundle import (
    auth,
    database,
    media_bindings,
    media_capture,
    media_files,
    media_jobs,
    media_subtitles,
    media_worker,
)
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.operations import intake, original
from fiber_bundle.payloads import Intake

POLICY = media_worker.MediaPolicy("sha256:" + "b" * 64)


@pytest.fixture
def media_scope(config, store):
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic-media-owner", False)
    vault = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vault, add=True, query=False, original=False)
    sources = []
    for kind, mime, data in [
        ("media", "audio/wav", b"synthetic-byte-binding"),
        ("subtitle", "application/x-subrip", RAW),
    ]:
        receipt = intake(
            config,
            db,
            client["id"],
            Intake(
                vault_id=vault,
                event_id=kind,
                source_key=kind,
                session_id="synthetic",
                raw_event=base64.b64encode(b"synthetic-event").decode(),
                assets=[
                    {
                        "role": "attachment",
                        "media_type": mime,
                        "original_name": kind + ".bin",
                        "data": base64.b64encode(data).decode(),
                    }
                ],
            ),
        )
        asset = db.execute(
            "SELECT va.asset_id FROM version_assets va WHERE version_id=? AND media_type=?",
            (receipt["source_version_id"], mime),
        ).fetchone()[0]
        sources.append((receipt["job_id"], asset, data))
    db.close()
    return client, vault, sources


def create(config, scope, **kw):
    client, _, sources = scope
    options = {"subtitle_receipt_id": sources[1][0], "subtitle_asset_id": sources[1][1]}
    options.update(kw)
    return media_jobs.create(
        config, client["id"], sources[0][0], sources[0][1], "audio_only", 3000, POLICY, **options
    )


def fixture_worker(config, policy, job_id, source, subtitle, spec, *, guard):
    guard()
    root = media_worker.directory(config, job_id)
    (root / "input").mkdir(parents=True)
    (root / "output").mkdir()
    media_files.write(root / "input", "source", source)
    media_files.write(root / "input", "request.json", media_files.canonical(spec))
    if subtitle is not None:
        media_files.write(root / "input", "subtitle", subtitle)
    from fiber_bundle.media_candidate import candidate

    value = candidate(source, subtitle, spec, PROBE)
    media_files.write(root / "output", "result.json", media_files.canonical(value))
    if subtitle:
        media_files.write(
            root / "output", "normalized.srt", media_subtitles.normalized(value["subtitle"]["cues"])
        )
    guard()
    return media_capture.verify(root / "output", source, subtitle, spec)


@pytest.mark.parametrize(
    "query,download,add",
    [(q, o, a) for q in (False, True) for o in (False, True) for a in (False, True)],
)
def test_create_permission_matrix(config, media_scope, query, download, add):
    with media_jobs.connection(config) as db:
        auth.set_grant(
            db, media_scope[0]["id"], media_scope[1], query=query, original=download, add=add
        )
    if add:
        assert create(config, media_scope)
    else:
        with pytest.raises(auth.Denied):
            create(config, media_scope)


def test_add_only_summary_query_only_result_and_original_independent(config, media_scope):
    client, vault, sources = media_scope
    cid = client["id"]
    job = create(config, media_scope)
    with patch.object(media_worker, "execute", side_effect=fixture_worker) as execution:
        value = media_jobs.run(config, cid, job)
        assert value["state"] == "complete" and "Literal" not in str(value)
        with pytest.raises(InvalidInput, match="no_replay"):
            media_jobs.run(config, cid, job)
        assert execution.call_count == 1
    with pytest.raises(auth.Denied):
        media_jobs.result(config, cid, job)
    with media_jobs.connection(config) as db:
        auth.set_grant(db, cid, vault, query=True, original=False, add=False)
        with pytest.raises(auth.Denied):
            original(
                config,
                db,
                cid,
                vault,
                db.execute(
                    "SELECT source_version_id FROM receipts WHERE job_id=?", (sources[0][0],)
                ).fetchone()[0],
                sources[0][1],
                8388608,
            )
    result = media_jobs.result(config, cid, job)
    assert result["subtitle"]["cues"][0]["text"] == "Literal synthetic caption."
    assert result["subtitle"]["provenance"] == "user_supplied_separate_asset"
    with media_jobs.connection(config) as db:
        for table in ("execution_runs", "model_calls", "document_tasks"):
            assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0


def test_other_owner_with_same_grants_cannot_claim_or_read(config, media_scope):
    job = create(config, media_scope)
    with media_jobs.connection(config) as db:
        other = auth.create_client(db, "other-synthetic", False)
        auth.set_grant(db, other["id"], media_scope[1], add=True, query=True, original=True)
    with pytest.raises(auth.Denied):
        media_jobs.run(config, other["id"], job)
    with pytest.raises(auth.Denied):
        media_jobs.status(config, other["id"], job)
    with pytest.raises(auth.Denied):
        media_jobs.create(config, other["id"], *media_scope[2][0][:2], "audio_only", 3000, POLICY)


@pytest.mark.parametrize("when", ["claim", "running", "finish", "delivery"])
def test_revocation_blocks_each_boundary_preserving_original(config, media_scope, when):
    cid, vault = media_scope[0]["id"], media_scope[1]
    job = create(config, media_scope)

    def revoke():
        with media_jobs.connection(config) as db:
            auth.set_grant(db, cid, vault, add=False, query=False, original=False)

    if when == "claim":
        revoke()

    def extraction(*args, **kw):
        if when == "running":
            revoke()
        value = fixture_worker(*args, **kw)
        if when == "finish":
            revoke()
        return value

    with patch.object(media_worker, "execute", side_effect=extraction):
        if when == "delivery":
            media_jobs.run(config, cid, job)
            revoke()
            with pytest.raises(auth.Denied):
                media_jobs.result(config, cid, job)
        else:
            with pytest.raises(auth.Denied):
                media_jobs.run(config, cid, job)
    with media_jobs.connection(config) as db:
        assert db.execute("SELECT count(*) FROM assets").fetchone()[0] > 0
        assert db.execute("SELECT state FROM media_tasks WHERE id=?", (job,)).fetchone()[0] == (
            "queued" if when == "claim" else "complete" if when == "delivery" else "failed"
        )


def test_cancel_queued_no_docker_and_running_never_finishes(config, media_scope):
    cid = media_scope[0]["id"]
    job = create(config, media_scope)
    with patch.object(media_worker, "stop_owned") as stop:
        assert media_jobs.stop(config, cid, job)["state"] == "cancelled"
        stop.assert_not_called()
    with pytest.raises(InvalidInput):
        media_jobs.run(config, cid, job)
    job = create(config, media_scope)

    def extraction(*args, **kw):
        with patch.object(media_worker, "stop_owned"):
            media_jobs.stop(config, cid, job)
        return fixture_worker(*args, **kw)

    with patch.object(media_worker, "execute", side_effect=extraction):
        with pytest.raises(InvalidInput):
            media_jobs.run(config, cid, job)
    assert media_jobs.status(config, cid, job)["state"] == "cancelled"


def test_unknown_running_requires_explicit_recovery_not_replay(config, media_scope):
    cid = media_scope[0]["id"]
    job = create(config, media_scope)
    with media_jobs.connection(config) as db:
        db.execute("UPDATE media_tasks SET state='running' WHERE id=?", (job,))
    with patch.object(media_worker, "execute") as execution:
        with pytest.raises(InvalidInput):
            media_jobs.run(config, cid, job)
        execution.assert_not_called()
    with patch.object(media_worker, "stop_owned"):
        assert media_jobs.stop(config, cid, job, recover=True)["state"] == "interrupted"
    with pytest.raises(InvalidInput):
        media_jobs.run(config, cid, job)


@pytest.mark.parametrize("change", ["type", "version", "bytes", "receipt"])
def test_source_drift_rejected(config, media_scope, change):
    cid, _, sources = media_scope
    job = create(config, media_scope)
    with media_jobs.connection(config) as db:
        if change == "type":
            db.execute(
                "UPDATE version_assets SET media_type='video/mp4' WHERE asset_id=?",
                (sources[0][1],),
            )
        elif change == "receipt":
            db.execute("UPDATE receipts SET state='failed' WHERE job_id=?", (sources[0][0],))
        elif change == "version":
            db.execute(
                "UPDATE receipts SET source_version_id=(SELECT source_version_id FROM receipts "
                "WHERE job_id=?) WHERE job_id=?",
                (sources[1][0], sources[0][0]),
            )
        else:
            row = db.execute(
                "SELECT relative_path FROM assets WHERE id=?", (sources[0][1],)
            ).fetchone()
            (config.state_root / row[0]).write_bytes(b"changed-source")
    with patch.object(media_worker, "execute") as execution:
        with pytest.raises((auth.Denied, IntegrityError, InvalidInput)):
            media_jobs.run(config, cid["id"], job)
        execution.assert_not_called()


def test_candidate_changed_after_completion_rejected(config, media_scope):
    cid = media_scope[0]["id"]
    job = create(config, media_scope)
    with patch.object(media_worker, "execute", side_effect=fixture_worker):
        media_jobs.run(config, cid, job)
    with media_jobs.connection(config) as db:
        auth.set_grant(db, cid, media_scope[1], add=True, query=True, original=False)
    path = media_worker.directory(config, job) / "output/result.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(IntegrityError):
        media_jobs.result(config, cid, job)
    with media_jobs.connection(config) as db:
        assert (
            db.execute("SELECT result_sha256 FROM media_tasks WHERE id=?", (job,)).fetchone()[0]
            == before
        )


def test_missing_subtitle_is_pending_and_no_model(config, media_scope):
    cid = media_scope[0]["id"]
    job = create(config, media_scope, subtitle_receipt_id=None, subtitle_asset_id=None)
    with patch.object(media_worker, "execute", side_effect=fixture_worker):
        media_jobs.run(config, cid, job)
    with media_jobs.connection(config) as db:
        auth.set_grant(db, cid, media_scope[1], add=True, query=True, original=False)
    assert media_jobs.result(config, cid, job)["reasons"] == ["subtitle_missing"]


def test_media_binding_rejects_document_and_event_assets(config, media_scope):
    with media_jobs.connection(config) as db:
        event = db.execute(
            "SELECT raw_asset_id FROM receipts WHERE job_id=?", (media_scope[2][0][0],)
        ).fetchone()[0]
        with pytest.raises(InvalidInput):
            media_bindings.source(config, db, media_scope[0]["id"], media_scope[2][0][0], event)


def test_failed_worker_stop_is_explicitly_recoverable_without_replay(config, media_scope):
    cid = media_scope[0]["id"]
    job = create(config, media_scope)
    with patch.object(
        media_worker, "execute", side_effect=InvalidInput("media_worker_stop_failed")
    ):
        with pytest.raises(InvalidInput):
            media_jobs.run(config, cid, job)
    assert media_jobs.status(config, cid, job)["error_code"] == "media_worker_stop_failed"
    with patch.object(media_worker, "stop_owned"):
        value = media_jobs.stop(config, cid, job, recover=True)
    assert value["state"] == "interrupted" and value["error_code"] == "media_worker_stop_failed"
    with pytest.raises(InvalidInput, match="no_replay"):
        media_jobs.run(config, cid, job)


@pytest.mark.parametrize("name", ["source", "subtitle", "request.json"])
def test_delivery_rechecks_retained_input_snapshots(config, media_scope, name):
    cid = media_scope[0]["id"]
    job = create(config, media_scope)
    with patch.object(media_worker, "execute", side_effect=fixture_worker):
        media_jobs.run(config, cid, job)
    with media_jobs.connection(config) as db:
        auth.set_grant(db, cid, media_scope[1], add=True, query=True, original=False)
    (media_worker.directory(config, job) / "input" / name).write_bytes(b"changed")
    with pytest.raises(InvalidInput, match="snapshot_changed"):
        media_jobs.result(config, cid, job)
