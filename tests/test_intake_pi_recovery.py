"""Legacy Pi upgrades recover between the real archive and channel sidecar commits."""

import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest
from test_articles import X_URL
from test_channel_intake import enable, receive, records
from test_channel_intake_content import grant_original
from test_unified_directory_roles import enable as enable_layout

from fiber_bundle import automatic_policy, database, execution_ledger, pi_processing
from fiber_bundle.channels import intake_extraction, intake_pi, intake_processing
from fiber_bundle.inputs import AssetInput, canonical
from fiber_bundle.objects import digest
from fiber_bundle.store import Store

pytest_plugins = ["test_channel_intake", "test_execution"]


@pytest.fixture
def legacy_analysis(config, entry, policy, monkeypatch):
    cid, vid = entry[1].client_id, entry[1].vault_id
    grant_original(config, entry)
    enable_layout(config, (cid, vid))
    monkeypatch.setattr(automatic_policy, "processing_route", lambda *args: policy)
    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            vid,
            0,
            automatic_policy.Policy(
                policy_version=2,
                enabled=True,
                client_id=cid,
                processing_mode="pi_skill",
                allow_model_source_content=True,
                allowed_kinds=["intake"],
            ),
        )
    enable(config, entry, policy_version=2, fetch_articles=True)
    batch = receive(config, entry, text=X_URL)
    intake_processing.advance(config, entry[0]["id"])
    body = b"# Synthetic source\nOriginal preserved text.\n"
    archive = Store(config)
    with database.locked(config), database.connect(config.state_root) as db:
        source = archive.ingest_locked(
            db,
            ingress_scope="synthetic-capture",
            event_id="capture",
            vault="personal",
            source_key="synthetic-source",
            raw_event=b"{}",
            items=(AssetInput(body, "note.md", "text/markdown", "body"),),
            session_scope="synthetic-capture",
            session_id=batch,
            owner_client_id=cid,
        )
    with database.connect(config.state_root) as db:
        run = pi_processing.prepare(
            config,
            db,
            policy,
            cid,
            vid,
            source.source_version_id,
            "article",
            digest(body),
            [{"id": "capture:0", "text": body.decode()}],
            allow_cloud=True,
        )
        pi_processing.materialize(config, pi_processing.get(db, run))
        (config.state_root / "tasks" / run / "draft.json").write_text(
            canonical(
                {
                    "schema": "fiber-processing-draft/v1",
                    "kind": "article",
                    "source_sha256": digest(body),
                    "summary": "Existing successful analysis",
                    "evidence_ids": ["capture:0"],
                    "gaps": ["text_only_analysis"],
                }
            )
        )
        execution_ledger.transition(db, run, "succeeded")
    frozen = {
        "configuration_sha256": policy.digest,
        "runs": {"0": run},
        "source_version_id": source.source_version_id,
        "source_sha256": digest(body),
        "state": "complete",
    }
    files = {
        "note.md": body,
        "analysis.md": b"# Legacy analysis\n",
        "publication-notes.json": canonical(
            {
                "schema": "fiber-document-notes/v1",
                "parts": [{"path": "analysis.md"}],
                "catalog": {"path": "note.md"},
            }
        ).encode(),
        "analysis-provenance.json": b'{"schema":"fiber-intake-analysis/v1"}',
    }
    with database.locked(config), database.connect(config.state_root) as db:
        receipt = archive.ingest_locked(
            db,
            ingress_scope="channel-intake-analysis:" + entry[0]["id"],
            event_id="legacy-attempt",
            vault="personal",
            source_key="intake-analysis:" + batch + ":0",
            raw_event=canonical(frozen).encode(),
            items=tuple(
                AssetInput(data, Path(name).name, "application/octet-stream", "attachment")
                for name, data in files.items()
            ),
            session_scope="channel-intake",
            session_id=batch,
            retention="knowledge",
            owner_client_id=cid,
        )
    intake_extraction.update(
        config,
        entry[0]["id"],
        batch,
        0,
        {
            "attempt_id": "legacy-attempt",
            "pi_processing": frozen,
            "state": "needs_review",
            "metadata": {"content_state": "review_required"},
            "receipt": asdict(receipt),
            "files": {
                name: {"sha256": digest(raw), "bytes": len(raw), "original_name": name}
                for name, raw in files.items()
            },
            "processing_gaps": ["text_only_analysis"],
            "review": {"id": "prior-review", "reason": "Prior review remains immutable"},
        },
    )
    return entry[0]["id"], batch, run, receipt


def saved_link(config, entry):
    return json.loads(records(config, entry)[0]["value"])["links"][0]


def test_legacy_archive_commit_then_sidecar_crash_reuses_event_and_successful_run(
    config, entry, legacy_analysis
):
    channel, batch, run, old_receipt = legacy_analysis
    before = saved_link(config, entry)
    with database.connect(config.state_root) as db:
        run_before = dict(execution_ledger.get_run(db, run))
        receipt_before = dict(
            db.execute("SELECT * FROM receipts WHERE job_id=?", (old_receipt.job_id,)).fetchone()
        )
    update = intake_pi.update

    def fail_after_archive(*args):
        if "receipt" in args[-1]:
            raise OSError("injected sidecar commit failure")
        update(*args)

    with (
        patch.object(intake_pi.execution, "execute") as execute,
        patch.object(pi_processing, "prepare") as prepare,
        patch("fiber_bundle.automatic_intake.resume_analysis") as resume,
    ):
        with (
            patch.object(intake_pi, "update", side_effect=fail_after_archive),
            pytest.raises(OSError, match="injected sidecar"),
        ):
            intake_pi.run(config, channel, batch, 0)
        interrupted = saved_link(config, entry)
        assert interrupted["receipt"] == before["receipt"]
        assert interrupted["review"] == before["review"]
        assert interrupted["pi_processing"]["state"] == "processing"
        assert interrupted["pi_processing"]["archive_event_id"] == "legacy-attempt:analysis-v2"
        assert interrupted["pi_processing"]["resume_analysis_cautions"] == ["text_only_analysis"]
        assert intake_pi.run(config, channel, batch, 0)
        recovered = saved_link(config, entry)
        assert recovered["pi_processing"]["analysis_contract"] == 2
        assert recovered["pi_processing"]["runs"] == before["pi_processing"]["runs"]
        assert recovered["review"] == before["review"]
        assert recovered["receipt"] != before["receipt"]
        resume.assert_called_once()
        assert resume.call_args.args[0] is config
        assert resume.call_args.args[2:] == (channel, batch, 0, ["text_only_analysis"])
        assert intake_pi.run(config, channel, batch, 0)
        assert saved_link(config, entry)["receipt"] == recovered["receipt"]
        execute.assert_not_called()
        prepare.assert_not_called()
    with database.connect(config.state_root) as db:
        receipts = db.execute(
            "SELECT event_id FROM receipts WHERE ingress_scope=? ORDER BY event_id",
            ("channel-intake-analysis:" + channel,),
        ).fetchall()
        assert [r[0] for r in receipts] == ["legacy-attempt", "legacy-attempt:analysis-v2"]
        assert dict(execution_ledger.get_run(db, run)) == run_before
        assert (
            dict(
                db.execute(
                    "SELECT * FROM receipts WHERE job_id=?", (old_receipt.job_id,)
                ).fetchone()
            )
            == receipt_before
        )
        Store(config).verify_receipt_locked(db, old_receipt.job_id)


@pytest.mark.parametrize("completed_v2", [False, True])
def test_published_and_completed_v2_are_not_rearchived(
    config, entry, legacy_analysis, completed_v2
):
    channel, batch, _, _ = legacy_analysis
    link = deepcopy(saved_link(config, entry))
    if completed_v2:
        link["pi_processing"]["analysis_contract"] = 2
    else:
        link["publication"] = {"id": "prior-publication", "state": "published"}
        link["state"] = "published"
    intake_extraction.update(config, channel, batch, 0, link)
    with (
        patch.object(Store, "ingest_locked") as archive,
        patch.object(intake_pi.execution, "execute") as execute,
        patch("fiber_bundle.automatic_intake.resume_analysis"),
    ):
        assert intake_pi.run(config, channel, batch, 0)
        archive.assert_not_called()
        execute.assert_not_called()
    current = saved_link(config, entry)
    assert current["receipt"] == link["receipt"]
    assert current["pi_processing"] == link["pi_processing"]
    assert current["review"] == link["review"]
