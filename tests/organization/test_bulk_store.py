import sqlite3

import pytest

from fiber_bundle.organization import store


def test_bulk_cache_roundtrip_and_failed_batch_rolls_back(config):
    config.state_root.mkdir()
    values = {str(i): {"values": [float(i)], "source": "derived"} for i in range(240)}
    store.put_many(config, "vectors", "v", values)
    assert store.get_many(config, "vectors", [*values, "absent"]) == values
    assert store.get_many(config, "vectors", []) == {}
    with store.connection(config, write=True) as db:
        db.execute(
            "CREATE TRIGGER reject_bad BEFORE INSERT ON vectors WHEN NEW.id='bad' "
            "BEGIN SELECT RAISE(ABORT,'test interruption'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.put_many(config, "vectors", "v", {"extra": {"x": 1}, "bad": {"x": 2}})
    assert store.get_many(config, "vectors", ["extra", "bad"]) == {}
    assert store.get_many(config, "vectors", list(values)) == values
