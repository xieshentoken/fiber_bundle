"""A real writer lease does not prevent the owner latching abort before publication."""

import json
import threading

from test_intake_abort_commands import command
from test_intake_separated_flow import capture

from fiber_bundle import (
    automatic_ingestion,
    database,
    feishu_receiver,
    publication_commit,
    publication_layout,
    weixin_inbox,
)

pytest_plugins = ["test_channel_intake", "test_execution"]


def test_abort_between_review_and_exclusive_file_commit(config, entry, policy, monkeypatch):
    batch = capture(config, entry, policy, monkeypatch, "basic", True)
    assert automatic_ingestion.tick(config)  # Structurally reviewed; publish queued.
    entered, proceed = threading.Event(), threading.Event()
    publish = publication_layout.publish
    errors = []

    def paused(*args, **kwargs):
        entered.set()
        assert proceed.wait(5), "abort capture was blocked by the publication writer lease"
        return publish(*args, **kwargs)

    monkeypatch.setattr(publication_layout, "publish", paused)

    def tick():
        try:
            automatic_ingestion.tick(config)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=tick)
    thread.start()
    try:
        assert entered.wait(5)
        assert command(config, entry, "abort " + batch)["state"] == "intake_abort_recorded"
    finally:
        proceed.set()
        thread.join(timeout=10)
    assert not thread.is_alive() and not errors, errors
    feishu_receiver.advance(config, entry[1])
    with database.connect(config.state_root) as db:
        location = db.execute("SELECT * FROM publication_locations").fetchone()
        assert location["state"] == "reconcile"
        assert json.loads(location["value"])["claims"] == {}
        root = config.vault_path("test vault/temp_vault1")
        assert not (root / location["entry_note_path"]).exists()
        assert db.execute("SELECT state FROM automatic_jobs").fetchone()[0] == "cancelled"


def test_publication_that_already_owns_commit_boundary_is_preserved(
    config, entry, policy, monkeypatch
):
    batch = capture(config, entry, policy, monkeypatch, "basic", True)
    automatic_ingestion.tick(config)
    entered, proceed = threading.Event(), threading.Event()
    check = publication_commit.check
    errors = []

    def paused(*args, **kwargs):
        entered.set()
        assert proceed.wait(5)
        return check(*args, **kwargs)

    monkeypatch.setattr(publication_commit, "check", paused)

    def publish():
        try:
            automatic_ingestion.tick(config)
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=publish)
    worker.start()
    assert entered.wait(5)
    control = threading.Thread(target=lambda: command(config, entry, "abort " + batch))
    control.start()
    proceed.set()
    worker.join(timeout=10)
    control.join(timeout=10)
    assert not worker.is_alive() and not control.is_alive() and not errors, errors
    feishu_receiver.advance(config, entry[1])
    automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        row = db.execute("SELECT * FROM publication_locations").fetchone()
        assert row["state"] == "published"
        assert (config.vault_path("test vault/temp_vault1") / row["entry_note_path"]).read_bytes()
        assert db.execute("SELECT state FROM automatic_jobs").fetchone()[0] == "complete"
    with weixin_inbox.connection(config, entry[1]) as db:
        assert (
            db.execute("SELECT state FROM fiber_intake_abort_commands").fetchone()[0] == "too_late"
        )
