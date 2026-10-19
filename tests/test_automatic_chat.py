"""Real journal/ZIP/Store/publication/index loop, without document workers or model calls."""

import json
import os
from pathlib import Path

import pytest
from test_channel_intake import enable, records
from test_channel_intake_content import grant_original
from test_chat_markdown import PNG, zipped
from test_feishu_ingress import freeze, payload
from test_publication_layout import activate

from fiber_bundle import (
    automatic_channel_sources,
    automatic_ingestion,
    automatic_jobs,
    automatic_policy,
    database,
    feishu_receiver,
    publication_locations,
    search,
    vault_save_contract,
    weixin_inbox,
    weixin_recovery,
)
from fiber_bundle.channels import intake_processing

pytest_plugins = ["test_channel_intake"]


@pytest.fixture(autouse=True)
def manual_ticks(monkeypatch):
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)


@pytest.fixture
def enabled(config, entry):
    grant_original(config, entry)
    settings = vault_save_contract.settings_wire(vault_save_contract.unified_settings())
    settings.pop("layout_mode")
    activate(config, entry[1].vault_id, **settings)
    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            entry[1].vault_id,
            0,
            automatic_policy.Policy(
                policy_version=2,
                enabled=True,
                direct_to_inbox=True,
                client_id=entry[1].client_id,
                allowed_kinds=["document"],
            ),
        )
    enable(config, entry, policy_version=2)
    return entry


def receive_zip(config, entry, data):
    event = payload("file", {"file_key": "synthetic-zip", "file_name": "聊天记录.zip"})
    feishu_receiver.capture(config, entry[1], event)
    with weixin_inbox.connection(config, entry[1]) as db:
        row = db.execute(
            "select * from events where event_id=?", (event["event"]["message"]["message_id"],)
        ).fetchone()
    weixin_recovery.complete(config, entry[1], row["job_id"], row["raw_sha256"], {"0": data})
    batch = freeze(config, entry[1])
    intake_processing.advance(config, entry[0]["id"])
    return batch


def drain(config):
    for _ in range(12):
        if not automatic_ingestion.tick(config):
            return
    raise AssertionError("automatic queue did not settle")


@pytest.mark.parametrize("sample", ["synthetic", "real"])
def test_zip_reaches_one_searchable_note_using_existing_attachment_paths(config, enabled, sample):
    entry = enabled
    if sample == "real":
        path = os.environ.get("FIBER_CHAT_ACCEPTANCE_ZIP")
        if not path:
            pytest.skip("explicit private real ZIP fixture not supplied")
        data = Path(path).read_bytes()
    else:
        data = zipped(
            {
                "聊天记录.txt": (
                    "合成聊天原文\n[图片] attachments/图片.jpg\n[文件] attachments/报告.pdf"
                ),
                "attachments/图片.jpg": PNG,
                "attachments/报告.pdf": b"%PDF-synthetic",
            }
        )
    receive_zip(config, entry, data)
    with database.connect(config.state_root) as db:
        before = db.execute(
            "select count(*) from vault_operations where kind='attachment_projection'"
        ).fetchone()[0]
    drain(config)
    with database.connect(config.state_root) as db:
        job = automatic_jobs.get(db, db.execute("select id from automatic_jobs").fetchone()[0])
        assert (job["state"], job["phase"]) == ("complete", "index"), job["reason"]
        location = publication_locations.get(db, "intake", job["source_id"])
        target = config.project_root / "test vault/temp_vault1" / location["entry_note_path"]
        note = target.read_text()
        assert target.parent.name == "Inbox" and "![[Attachments/images/" in note
        assert len(job["value"]["references"]) >= 2
        if sample == "synthetic":
            assert "[[Attachments/pristin/" in note and "报告.pdf]]" in note
        assert (
            db.execute(
                "select count(*) from vault_operations where kind='attachment_projection'"
            ).fetchone()[0]
            == before
        )
        assert db.execute("select count(*) from model_calls").fetchone()[0] == 0
        assert db.execute("select count(*) from execution_runs").fetchone()[0] == 0
        assert db.execute("select count(*) from source_imports").fetchone()[0] == 0
        hits = search.vault_search(config, db, entry[1].client_id, "聊天", [entry[1].vault_id])[
            "results"
        ]
        assert len(hits) == 1
        assert hits[0]["note_path"] == location["entry_note_path"]
        assert hits[0]["review_type"] == "system"
    value = json.loads(records(config, entry)[0]["value"])
    with database.locked(config), database.connect(config.state_root) as db:
        automatic_channel_sources.admit(config, db, entry[1].client_id, entry[1].vault_id, value)
    drain(config)
    assert len(list(target.parent.glob("*.md"))) == 1
    with database.connect(config.state_root) as db:
        assert db.execute("select count(*) from automatic_jobs").fetchone()[0] == 1
        assert db.execute("select count(*) from publication_locations").fetchone()[0] == 1


def test_missing_attachment_stays_reviewable_and_never_creates_a_complete_note(config, enabled):
    receive_zip(config, enabled, zipped({"聊天记录.txt": "[文件] missing.pdf"}))
    drain(config)
    with database.connect(config.state_root) as db:
        row = db.execute("select state,reason from automatic_jobs").fetchone()
        assert tuple(row) == ("needs_review", "chat_attachment_missing")
        assert db.execute("select count(*) from publication_locations").fetchone()[0] == 0


@pytest.mark.parametrize("sample", ["synthetic", "real_missing_images"])
def test_missing_images_publish_partial_text_and_keep_previous_review(config, enabled, sample):
    import os
    from pathlib import Path

    from fiber_bundle import automatic_review

    if sample == "real_missing_images":
        path = os.environ.get("FIBER_CHAT_MISSING_ACCEPTANCE_ZIP")
        if not path:
            pytest.skip("private missing-image ZIP requires explicit opt-in")
        raw = Path(path).read_bytes()
    else:
        raw = zipped({"聊天记录.txt": "完整文字\n[图片] missing.jpg\n最后一段"})
    receive_zip(config, enabled, raw)
    with database.connect(config.state_root) as db:
        job = automatic_jobs.get(db, db.execute("select id from automatic_jobs").fetchone()[0])
        old_review = automatic_review.issue(
            config,
            db,
            job,
            {
                "decision": "uncertain",
                "source_sha256": job["value"]["chat_archive"]["sha256"],
                "result_sha256": "a" * 64,
            },
        )
        old_value = db.execute(
            "select value from automatic_reviews where id=?", (old_review,)
        ).fetchone()[0]
        automatic_jobs.settle(db, job, "needs_review", "publish", "chat_attachment_missing")
        original_job = job["id"]
        value = json.loads(records(config, enabled)[0]["value"])
        automatic_channel_sources.admit(
            config, db, enabled[1].client_id, enabled[1].vault_id, value
        )
        job = automatic_jobs.get(db, value["automatic_imports"][0]["job_id"])
        assert job["value"]["supersedes_job"] == original_job
    drain(config)
    with database.connect(config.state_root) as db:
        job = automatic_jobs.get(db, job["id"])
        assert (job["state"], job["phase"]) == ("complete", "index"), job["reason"]
        location = publication_locations.get(db, "intake", job["source_id"])
        path = config.project_root / "test vault/temp_vault1" / location["entry_note_path"]
        note = path.read_text()
        assert "图片缺失" in note and "completeness_status: partial" in note
        assert "![[" not in note and path.parent.name == "Inbox"
        assert job["value"]["missing_images"]
        assert job["value"]["review_id"] != old_review
        assert automatic_jobs.get(db, original_job)["state"] == "needs_review"
        assert (
            db.execute("select value from automatic_reviews where id=?", (old_review,)).fetchone()[
                0
            ]
            == old_value
        )
        review = json.loads(
            db.execute(
                "select value from automatic_reviews where id=?", (job["value"]["review_id"],)
            ).fetchone()[0]
        )
        assert review["decision"] == "accept" and review["completeness_status"] == "partial"
        assert "chat_image_missing" in review["known_gaps"]
        assert db.execute("select count(*) from model_calls").fetchone()[0] == 0
    drain(config)
    assert len(list(path.parent.glob("*.md"))) == 1


def test_edited_attachment_blocks_publication_and_preserves_the_edit(config, enabled):
    receive_zip(config, enabled, zipped({"聊天记录.txt": "[图片] 图.jpg", "图.jpg": PNG}))
    with database.connect(config.state_root) as db:
        row = db.execute(
            "select payload from vault_operations where kind='attachment_projection' "
            "and json_extract(payload,'$.media_type')='image/png'"
        ).fetchone()
    relative = json.loads(row[0])["path"]
    file = config.project_root / "test vault/temp_vault1" / relative
    file.write_bytes(b"human edit must survive")
    drain(config)
    assert file.read_bytes() == b"human edit must survive"
    with database.connect(config.state_root) as db:
        assert (
            db.execute("select reason from automatic_jobs").fetchone()[0]
            == "attachment_projection_copy_diverged"
        )
        assert db.execute("select count(*) from publication_locations").fetchone()[0] == 0


def test_direct_save_off_keeps_a_reviewed_candidate_until_explicit_publish(config, enabled, http):
    with database.connect(config.state_root) as db:
        policy = automatic_policy.get(db, enabled[1].vault_id)
        settings = automatic_policy.Policy(**{**policy["settings"], "direct_to_inbox": False})
        automatic_policy.save(config, db, enabled[1].vault_id, policy["revision"], settings)
    receive_zip(config, enabled, zipped({"聊天记录.txt": "完整合成原文"}))
    drain(config)
    with database.connect(config.state_root) as db:
        row = db.execute("select id,state from automatic_jobs").fetchone()
        assert row["state"] == "reviewed"
        assert db.execute("select count(*) from publication_locations").fetchone()[0] == 0
    result = http.post(
        f"/admin/vault-catalog/{enabled[1].vault_id}/automatic-jobs/{row['id']}/publish"
    )
    assert result.status_code == 200
    drain(config)
    with database.connect(config.state_root) as db:
        assert db.execute("select state from automatic_jobs").fetchone()[0] == "complete"
