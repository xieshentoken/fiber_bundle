"""The background scheduler completes admitted work without a browser or duplicate publication."""

import time
from unittest.mock import patch

from test_automatic_ingestion import setup
from test_source_processing import WORKERS, worker

from fiber_bundle import (
    auth,
    automatic_ingestion,
    automatic_jobs,
    database,
    source_import_execution,
    source_processing_policy,
)
from fiber_bundle.inputs import AssetInput
from fiber_bundle.store import Store


def test_real_scheduler_runs_durable_phases_without_browser(config, store):
    from fiber_bundle import source_import_existing

    with database.connect(config.state_root) as db:
        client = auth.create_client(db, "synthetic-scheduler-owner", False)
        ids = {r["name"]: r["id"] for r in db.execute("SELECT id,name FROM vaults")}
        auth.set_grant(db, client["id"], ids["personal"], query=True, original=True, add=True)
    cid, vid = setup(config, (client["id"], {}, ids))
    source_processing_policy.save(config, None, WORKERS, None)
    saved = Store(config).ingest(
        ingress_scope="synthetic",
        event_id="background",
        vault="personal",
        source_key="background",
        raw_event=b"event",
        items=(
            AssetInput(
                b"# Browser-independent token 12 watts", "sample.md", "text/markdown", "body"
            ),
        ),
        session_scope="synthetic",
        session_id="background",
        retention="knowledge",
    )
    with database.connect(config.state_root) as db:
        db.execute("UPDATE receipts SET owner_client_id=? WHERE job_id=?", (cid, saved.job_id))
        asset = db.execute(
            "SELECT asset_id FROM version_assets WHERE version_id=?", (saved.source_version_id,)
        ).fetchone()[0]
        item = source_import_existing.create(
            config, db, cid, vid, saved.job_id, asset, "ordinary", {}
        )
        job = automatic_jobs.enqueue(config, db, cid, vid, "document", item["id"])

    def launch(config, db, client, identity):
        run = source_import_execution.prepare(config, db, client, identity)
        db.execute("UPDATE source_task_bindings SET launch_state='launched' WHERE run_id=?", (run,))

    runtime = automatic_ingestion.Runtime(config)
    with patch("fiber_bundle.source_import_execution.launch", side_effect=launch):
        runtime.start()
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                with database.connect(config.state_root) as db:
                    record = automatic_jobs.get(db, job)
                if record["phase"] == "review":
                    break
                time.sleep(0.05)
            with patch("fiber_bundle.document_worker.execute_document", side_effect=worker):
                source_import_execution.execute(config, record["value"]["run_id"])
            while time.monotonic() < deadline:
                with database.connect(config.state_root) as db:
                    record = automatic_jobs.get(db, job)
                if record["state"] == "complete":
                    break
                time.sleep(0.05)
            assert record["state"] == "complete", record
        finally:
            runtime.close()
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM document_publications").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM document_reviews").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    assert not runtime.thread.is_alive()
