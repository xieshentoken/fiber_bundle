"""Opt-in synthetic core workflow fixtures, never loaded by the application.

Text extraction and graph output are replayed worker fixtures. Admission, validation,
review, publication and MOC writes use production contracts and APIs. This does not
certify extraction accuracy, graph inference or remote platform delivery.
"""

import hashlib
import json
from dataclasses import replace
from unittest.mock import patch

from knowledge_graph.test_product import moc_build
from test_feishu_ingress import freeze, payload
from test_source_processing import complete, prepare

from fiber_bundle import auth, database, feishu_receiver, note_identities, query_policy_store
from fiber_bundle.inputs import AssetInput
from fiber_bundle.knowledge_graph import intake, jobs, policy
from fiber_bundle.knowledge_graph.contracts import Engine
from fiber_bundle.store import Store
from fiber_bundle.weixin_events import Binding


def prepare_core(config):
    db = database.connect(config.state_root)
    client = auth.create_client(db, "G2 合成资料身份", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vid, query=True, original=True, add=True)
    # Both synthetic Vaults are intentionally writable by this test identity,
    # so rendered scope replacement exercises real permission-filtered choices.
    for row in db.execute("SELECT id FROM vaults WHERE status='active'").fetchall():
        auth.set_grant(db, client["id"], row["id"], query=True, original=True, add=True)
    db.close()
    route = query_policy_store.current(config)
    db = database.connect(config.state_root)
    receipt = Store(config).ingest_locked(
        db,
        ingress_scope="client:" + client["id"],
        event_id="gui-source",
        vault="personal",
        source_key="gui-source",
        raw_event=b'{"synthetic":true}',
        items=(
            AssetInput(
                "青松合成资料\n测试压力 2.4 MPa。\n".encode(), "gui.txt", "text/plain", "body"
            ),
        ),
        session_scope="client:" + client["id"],
        session_id="gui-core",
        retention="knowledge",
        owner_client_id=client["id"],
    )
    _, run_id = prepare(
        config, db, route.cloud_answer, client["id"], vid, receipt.job_id, "gui-review"
    )
    complete(config, db, route.cloud_answer, run_id)
    db.close()
    selected = []
    for i in range(3):
        note = config.project_root / "test vault/temp_vault1" / f"gui-graph-{i}.md"
        note.write_text(f"# 青松关联资料{i}\n合成测试压力 2.4 MPa。\n")
        digest = hashlib.sha256(note.read_bytes()).hexdigest()
        tracked = note_identities.run(
            config, "Bearer " + client["token"], vid, "track", note.name, digest
        )
        selected.append(intake.Selection(tracked["note_identity"], digest))
    profile = policy.Profile(1, True, "sha256:" + "1" * 64, Engine())
    with database.locked(config):
        policy.save(config, profile, None)
    job = jobs.submit(config, client["id"], vid, tuple(selected))
    with patch("fiber_bundle.knowledge_graph.jobs.worker.run_build", moc_build):
        jobs.execute(config, client["id"], vid, job["id"])
    binding = replace(
        Binding("synthetic-bot", "synthetic-owner", client["id"], vid),
        account_id="feishu:cli_synthetic",
        owner_id="ou_owner",
    )
    feishu_receiver.initialize(config, binding)
    feishu_receiver.capture(config, binding, payload(body={"text": "G2 合成批次，测试压力2.4 MPa"}))
    batch = freeze(config, binding)
    manifest = {
        "synthetic": True,
        "real_model_calls": 0,
        "remote_platform_delivery": False,
        "worker_outputs": "replayed text extraction and graph; production validation and writes",
        "client_id": client["id"],
        "vault_id": vid,
        "document_run": run_id,
        "graph_job": job["id"],
        "batch_id": batch,
    }
    (config.project_root / "core-workflow-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
