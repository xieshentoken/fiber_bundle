"""Admin clicks schedule genuine Store work with stable identities and observable failures."""

import json

import pytest
from test_channel_intake import count, enable, records
from test_feishu_ingress import freeze, payload

from fiber_bundle import feishu_receiver, weixin_store
from fiber_bundle.channels import intake_processing

pytest_plugins = ["test_channel_intake"]


@pytest.fixture(autouse=True)
def manual_ticks(monkeypatch):
    monkeypatch.setattr("fiber_bundle.automatic_ingestion.Runtime.start", lambda self: None)


def capture(config, entry):
    for i in range(2):
        feishu_receiver.capture(
            config, entry[1], payload(body={"text": f"Synthetic source {i}"}, identity=str(i))
        )
    return freeze(config, entry[1])


@pytest.mark.parametrize("choice,expected", [("save_one", 1), ("save_separately", 2)])
def test_click_schedules_stable_grouping_even_with_automatic_switch_off(
    config, entry, http, choice, expected
):
    enable(config, entry, enabled=False)
    batch = capture(config, entry)
    url = f"/admin/batches/{batch}/choice"
    result = http.post(url, json={"action": choice})
    assert result.status_code == 200, result.json()
    assert result.json()["intake"]["queued"] == 1
    assert count(config, "receipts") == 0
    assert http.get("/admin/batches").json()[0]["intake"]["state"] == "queued"
    assert http.post(url, json={"action": choice}).status_code == 200
    assert len(records(config, entry)) == 1
    assert intake_processing.advance(config, entry[0]["id"]) == 1
    assert count(config, "receipts") == expected
    assert http.post(url, json={"action": choice}).status_code == 200
    assert intake_processing.advance(config, entry[0]["id"]) == 0
    assert count(config, "receipts") == expected
    listed = http.get("/admin/batches").json()[0]
    assert listed["intake"]["state"] == listed["store_state"] == "stored"
    assert count(config, "model_calls") == 0


def test_archive_failure_is_visible_and_explicit_continue_reuses_original_job(
    config, entry, http, monkeypatch
):
    enable(config, entry)
    batch = capture(config, entry)
    url = f"/admin/batches/{batch}/choice"
    assert http.post(url, json={"action": "save_one"}).status_code == 200
    original = weixin_store.transfer

    def fail(*args, **kwargs):
        raise OSError("synthetic private details must not reach UI")

    monkeypatch.setattr(weixin_store, "transfer", fail)
    with pytest.raises(OSError):
        intake_processing.advance(config, entry[0]["id"])
    listed = http.get("/admin/batches").json()[0]
    assert listed["intake"]["state"] == "needs_review"
    assert listed["intake"]["reason"] == "intake_archive_interrupted"
    assert "private details" not in json.dumps(listed)
    monkeypatch.setattr(weixin_store, "transfer", original)
    assert http.post(url, json={"action": "save_one"}).status_code == 200
    assert intake_processing.advance(config, entry[0]["id"]) == 1
    assert count(config, "receipts") == 1
    assert http.post(url, json={"action": "save_separately"}).status_code == 422
    assert count(config, "receipts") == 1


def test_originals_saved_by_legacy_path_are_not_imported_again(config, entry, http):
    from test_channel_intake import receive

    from fiber_bundle import weixin_batches

    enable(config, entry)
    batch = receive(config, entry)
    weixin_batches.choose(config, entry[1], batch, "save_one")
    weixin_store.transfer(config, entry[1], batch)
    assert (
        http.post(f"/admin/batches/{batch}/choice", json={"action": "save_one"}).status_code == 200
    )
    assert records(config, entry) == []
    assert count(config, "receipts") == 1


def test_cancelled_queue_does_not_archive_and_revoked_grant_blocks_work(config, entry, http):
    enable(config, entry)
    batch = capture(config, entry)
    url = f"/admin/batches/{batch}/choice"
    assert http.post(url, json={"action": "save_one"}).status_code == 200
    assert http.post(url, json={"action": "cancel"}).status_code == 200
    assert intake_processing.advance(config, entry[0]["id"]) == 0
    assert count(config, "receipts") == 0


def test_missing_target_has_actionable_error_and_leaves_materials_untouched(config, entry, http):
    batch = capture(config, entry)
    response = http.post(f"/admin/batches/{batch}/choice", json={"action": "save_one"})
    assert response.status_code == 422
    assert response.json()["error"] == "intake_target_required"
    assert "目标知识库" in response.json()["message"]
    assert count(config, "receipts") == 0


def test_competing_writer_cannot_split_original_archive_from_queue_settlement(
    config, entry, http, monkeypatch
):
    """A publication writer arriving after Store commit must not strand this batch."""
    import fcntl
    import os

    from fiber_bundle import database

    enable(config, entry)
    batch = capture(config, entry)
    assert (
        http.post(f"/admin/batches/{batch}/choice", json={"action": "save_one"}).status_code == 200
    )
    original = weixin_store.transfer
    contender = os.open(config.state_root / "storage.lock", os.O_RDWR)

    def transfer_then_contend(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass  # The archive keeps its lease through progress settlement.
        return result

    monkeypatch.setattr(weixin_store, "transfer", transfer_then_contend)
    try:
        with database.lock_wait(0.05):
            assert intake_processing.advance(config, entry[0]["id"]) == 1
    finally:
        os.close(contender)
    listed = http.get("/admin/batches").json()[0]
    assert listed["intake"]["state"] == "stored"
    assert count(config, "receipts") == 1
