import pytest
from test_automatic_chat import drain, receive_zip
from test_automatic_chat import enabled as enabled
from test_channel_intake import entry as entry
from test_chat_markdown import zipped
from test_weixin_inbox import binding as binding

from fiber_bundle import database
from fiber_bundle.config import InvalidInput
from fiber_bundle.suppliers import published, service, store
from fiber_bundle.suppliers.contracts import Policy, SavePolicy


def test_new_chat_after_activation_is_observed_once_and_keeps_its_zip(config, enabled):
    cid, vid = enabled[1].client_id, enabled[1].vault_id
    with database.connect(config.state_root) as db:
        service.save_policy(
            config,
            db,
            SavePolicy(
                client_id=cid, vault_id=vid, expected_revision=0, settings=Policy(enabled=True)
            ),
        )
        after = store.get(config, vid, "activation", "current")["after"]
    raw = zipped({"聊天记录.txt": "Synthetic supplier Diamond Alpha 590 CNY/100g"})
    receive_zip(config, enabled, raw)
    drain(config)
    with database.locked(config), database.connect(config.state_root) as db:
        assert published.enqueue(config, db, cid, vid, after) == 1
        assert published.enqueue(config, db, cid, vid, after) == 0
        job = store.rows(config, vid, "job")[0]
        from fiber_bundle.suppliers import ingestion, read

        content, name, mime = read.original(config, db, cid, vid, job["source"]["id"])
        assert content == raw and name.endswith(".zip")
        path = config.project_root / "test vault/temp_vault1" / job["source"]["path"]
        path.write_text(path.read_text() + "\nUser edited")
        with pytest.raises(InvalidInput, match="supplier_sources_changed"):
            ingestion.current_source(config, db, cid, vid, job["source"])
        with pytest.raises(InvalidInput, match="supplier_sources_changed"):
            read.summary(config, db, cid, vid)


def test_idle_runtime_reuses_publication_metadata_instead_of_rescanning(
    config, enabled, monkeypatch
):
    from fiber_bundle.suppliers.runtime import Runtime

    cid, vid = enabled[1].client_id, enabled[1].vault_id
    with database.connect(config.state_root) as db:
        service.save_policy(
            config,
            db,
            SavePolicy(
                client_id=cid, vault_id=vid, expected_revision=0, settings=Policy(enabled=True)
            ),
        )
    scans = []
    monkeypatch.setattr(published, "enqueue", lambda *args: scans.append(1))
    runtime = Runtime(config)
    runtime.tick()
    runtime.last_scan.clear()
    runtime.tick()
    assert scans == [1]
