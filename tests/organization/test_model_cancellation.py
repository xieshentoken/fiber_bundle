import threading
import time

from test_model_ledger import configured

from fiber_bundle import database, model_gateway
from fiber_bundle.config import InvalidInput
from fiber_bundle.organization import model_review
from fiber_bundle.organization import store as ledger

pytest_plugins = ["test_execution"]


def test_pause_releases_waiter_and_late_result_only_settles_original_call(
    config, store, policy, monkeypatch
):
    selected = configured(config, policy)
    entered, release = threading.Event(), threading.Event()
    job = {
        "id": "pending-model",
        "vault_id": "vault",
        "maintenance": "test",
        "state": "running",
        "generation": 0,
    }
    ledger.put(config, "jobs", job["id"], "vault", job)

    def complete(*a):
        entered.set()
        assert release.wait(5)
        return {
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            "choices": [{"message": {"content": "cached answer"}}],
        }

    monkeypatch.setattr(model_gateway, "prepare", lambda p, v: v)
    monkeypatch.setattr(model_gateway, "complete", complete)
    outcome = []

    def invoke():
        try:
            model_review.call(config, job, selected, [], "key", lambda: None)
        except InvalidInput as error:
            outcome.append(str(error))

    waiter = threading.Thread(target=invoke)
    waiter.start()
    try:
        assert entered.wait(3)
        start = time.monotonic()
        paused = {**job, "state": "paused", "generation": 1}
        with database.locked(config):
            ledger.put(config, "jobs", job["id"], "vault", paused)
        waiter.join(1)
        assert not waiter.is_alive() and time.monotonic() - start < 1
        assert outcome == ["maintenance_cancelled"]
        assert ledger.get(config, "model_reviews", "key")["state"] == "sending"
        release.set()
        until = time.monotonic() + 3
        while (
            ledger.get(config, "model_reviews", "key")["state"] == "sending"
            and time.monotonic() < until
        ):
            time.sleep(0.02)
        assert ledger.get(config, "model_reviews", "key")["reply"] == "cached answer"
        assert ledger.get(config, "jobs", job["id"]) == paused
        with database.connect(config.state_root) as db:
            row = db.execute(
                "select state,input_tokens,output_tokens from model_configuration_calls"
            ).fetchone()
            assert tuple(row) == ("succeeded", 7, 3)
    finally:
        release.set()
        waiter.join(3)
