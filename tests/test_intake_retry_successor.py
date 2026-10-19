"""Explicit source retries replace only safe cancelled work and retain receipts/history."""

import json

import pytest
from test_article_fetch import Reply, install
from test_articles import PNG, current_html
from test_channel_intake import records
from test_intake_separated_flow import capture
from test_intake_staged_receipts import notify as notify

from fiber_bundle import automatic_ingestion, automatic_jobs, automatic_policy, database
from fiber_bundle.channels import intake_extraction
from fiber_bundle.config import InvalidInput

pytest_plugins = ["test_channel_intake", "test_execution"]


def cancel_job(config, batch):
    with database.connect(config.state_root) as db:
        identity = db.execute(
            "SELECT id FROM automatic_jobs WHERE json_extract(value,'$.intake.batch')=?",
            (batch,),
        ).fetchone()[0]
        job = automatic_jobs.get(db, identity)
        automatic_jobs.settle(db, job, "cancelled")
        return dict(db.execute("SELECT * FROM automatic_jobs WHERE id=?", (identity,)).fetchone())


def rec(config, entry):
    row = records(config, entry)[0]
    return row, json.loads(row["value"])


def extract(config, entry, monkeypatch):
    install(
        monkeypatch,
        [
            Reply(200, current_html(), [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    assert intake_extraction.advance(config, entry[0]["id"])


@pytest.mark.parametrize("direct", [False, True])
def test_cancelled_capture_retries_as_successor_and_reports_current_result(
    config, entry, policy, monkeypatch, notify, direct
):
    calls, _, deliver = notify
    batch = capture(config, entry, policy, monkeypatch, "basic", direct)
    deliver()
    old = cancel_job(config, batch)
    deliver()
    assert "已取消" in calls[-1]
    _, before = rec(config, entry)
    updates = before["receipt_updates"]
    original_receipts = before["receipts"]
    row, _ = rec(config, entry)
    intake_extraction.retry(config, entry[0]["id"], batch, 0, row["revision"])
    _, queued = rec(config, entry)
    attempt = queued["links"][0]["attempt_id"]
    automatic_ingestion.tick(config)
    deliver(2)
    assert len(calls) == 2  # Old cancellation cannot become a fresh result for pending work.
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM publication_locations").fetchone()[0] == 0
        successor = db.execute("SELECT * FROM automatic_jobs WHERE id!=?", (old["id"],)).fetchone()
        assert json.loads(successor["value"])["supersedes_job"] == old["id"]
        assert json.loads(successor["value"])["intake"]["attempt_id"] == attempt
    extract(config, entry, monkeypatch)
    for _ in range(5):
        automatic_ingestion.tick(config)
    deliver(3)
    _, after = rec(config, entry)
    assert after["links"][0]["attempt_id"] == attempt
    assert after["receipts"] == original_receipts
    assert all(after["receipt_updates"][k] == v for k, v in updates.items())
    assert len(calls) == 3
    assert ("笔记已保存入库" if direct else "等待人工核验") in calls[-1]
    with database.connect(config.state_root) as db:
        assert (
            dict(db.execute("SELECT * FROM automatic_jobs WHERE id=?", (old["id"],)).fetchone())
            == old
        )
        assert db.execute("SELECT count(*) FROM automatic_jobs").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM publication_locations").fetchone()[0] == int(direct)
    if direct:
        note = (
            config.vault_path("test vault/temp_vault1")
            / after["links"][0]["publication"]["entry_note_path"]
        )
        assert note.read_text().strip()


def test_new_failure_is_not_masked_by_previous_cancelled_job(
    config, entry, policy, monkeypatch, notify
):
    calls, _, deliver = notify
    batch = capture(config, entry, policy, monkeypatch, "basic", True)
    deliver()
    cancel_job(config, batch)
    row, _ = rec(config, entry)
    intake_extraction.retry(config, entry[0]["id"], batch, 0, row["revision"])
    intake_extraction.update(
        config,
        entry[0]["id"],
        batch,
        0,
        {"state": "needs_review", "reason": "video_skill_visual_response_invalid"},
    )
    automatic_ingestion.tick(config)
    deliver(2)
    assert "失败" in calls[-1] and "已取消" not in calls[-1]
    # Retrying this failed successor creates one new leaf; older attempts are not revived.
    row, _ = rec(config, entry)
    intake_extraction.retry(config, entry[0]["id"], batch, 0, row["revision"])
    with database.connect(config.state_root) as db:
        jobs = [automatic_jobs.get(db, r[0]) for r in db.execute("SELECT id FROM automatic_jobs")]
        assert len(jobs) == 3 and sum(j["state"] == "cancelled" for j in jobs) == 1
    extract(config, entry, monkeypatch)
    for _ in range(5):
        automatic_ingestion.tick(config)
    deliver(2)
    assert "笔记已保存入库" in calls[-1]


@pytest.mark.parametrize("cause", ["abort", "policy", "publish"])
def test_retry_does_not_override_abort_changed_policy_or_uncertain_publication(
    config, entry, policy, monkeypatch, cause
):
    batch = capture(config, entry, policy, monkeypatch, "basic", True)
    old = cancel_job(config, batch)
    if cause == "abort":
        from test_intake_abort_commands import command

        command(config, entry, "abort " + batch)
    elif cause == "policy":
        with database.connect(config.state_root) as db:
            current = automatic_policy.get(db, old["vault_id"])
            settings = automatic_policy.Policy(**{**current["settings"], "direct_to_inbox": False})
            automatic_policy.save(config, db, old["vault_id"], current["revision"], settings)
    else:
        # A cancelled job at publication requires proof-based reconciliation, never recapture.
        with database.connect(config.state_root) as db:
            db.execute("UPDATE automatic_jobs SET phase='publish' WHERE id=?", (old["id"],))
    row, _ = rec(config, entry)
    with pytest.raises(InvalidInput):
        intake_extraction.retry(config, entry[0]["id"], batch, 0, row["revision"])
    assert rec(config, entry)[0]["value"] == row["value"]
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM automatic_jobs").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


def test_abort_during_successor_extraction_still_prevents_publication(
    config, entry, policy, monkeypatch
):
    from test_intake_abort_commands import command

    from fiber_bundle import feishu_receiver

    batch = capture(config, entry, policy, monkeypatch, "basic", True)
    cancel_job(config, batch)
    row, _ = rec(config, entry)
    intake_extraction.retry(config, entry[0]["id"], batch, 0, row["revision"])
    assert command(config, entry, "abort " + batch)["state"] == "intake_abort_recorded"
    feishu_receiver.advance(config, entry[1])
    for _ in range(5):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM publication_locations").fetchone()[0] == 0
        assert {r[0] for r in db.execute("SELECT state FROM automatic_jobs")} == {"cancelled"}
