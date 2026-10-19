"""An interrupted capture cannot block later batches; old batches remain untouched."""

import json

from test_channel_intake import enable, receive

from fiber_bundle.channels import intake_extraction, intake_processing, store
from fiber_bundle.config import InvalidInput

pytest_plugins = ["test_channel_intake"]


def test_interrupted_head_does_not_block_next_capture(config, entry, monkeypatch):
    enable(config, entry, fetch_articles=True)
    first = receive(config, entry, text="https://mp.weixin.qq.com/s/old", identity="old")
    assert intake_processing.advance(config, entry[0]["id"]) == 1
    with store.opened(config) as db:
        row = db.execute(
            "SELECT value FROM channel_intake_records WHERE batch_id=?", (first,)
        ).fetchone()
        value = json.loads(row[0])
        value["links"][0].update(state="extracting", attempt_id="interrupted")
        raw = json.dumps(value)
        db.execute("UPDATE channel_intake_records SET value=? WHERE batch_id=?", (raw, first))
    receive(config, entry, text="https://mp.weixin.qq.com/s/new", identity="new")
    assert intake_processing.advance(config, entry[0]["id"]) == 1
    called = []

    def acquire(url, *args):
        called.append(url)
        raise InvalidInput("intake_extractor_unavailable")

    monkeypatch.setattr(intake_extraction.intake_fetch, "acquire", acquire)
    assert intake_extraction.advance(config, entry[0]["id"])
    assert called == ["https://mp.weixin.qq.com/s/new"]
    with store.readonly(config) as db:
        assert (
            db.execute(
                "SELECT value FROM channel_intake_records WHERE batch_id=?", (first,)
            ).fetchone()[0]
            == raw
        )
