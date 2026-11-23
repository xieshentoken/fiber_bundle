"""MOC-only channel queries use registered members, without a whole-Vault index."""

import asyncio
import copy
import hashlib
import json
import sqlite3
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_channel_natural_query import model_channel as model_channel
from test_channel_natural_query import result
from test_message_channels import bind
from test_message_channels import channel as channel
from test_model_configuration import vault_keychain as vault_keychain

from fiber_bundle import (
    auth,
    database,
    moc_associations,
    moc_files,
    model_gateway,
    note_identities,
    note_search_cache,
)
from fiber_bundle.channels import configuration, dispatch, natural_query, query, query_scope, store
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.inputs import canonical


def configure_moc(config, entry, **changes):
    with store.opened(config) as side:
        prior = store.get(side, entry["id"])
        side.execute("UPDATE channels SET state='paused' WHERE id=?", (entry["id"],))
    settings = {
        **prior["value"],
        "purpose": "query",
        "policy_version": 2,
        "query_note_scope": "moc_members",
        **changes,
    }
    with database.connect(config.state_root) as main:
        saved = configuration.save(
            config,
            main,
            configuration.Save(
                id=entry["id"],
                expected_revision=prior["revision"],
                settings=configuration.Settings.model_validate(settings),
            ),
        )
    bind(config, saved)
    return saved


def project_moc(config, scope, revision):
    client, vid, root, mid, _ = scope
    target = root / "Topic MOC.md"
    planned = moc_files.run(
        config,
        "Bearer " + client["token"],
        vid,
        "plan-file",
        mid,
        revision=revision,
        relative=target.name,
        expected_sha256=hashlib.sha256(target.read_bytes()).hexdigest()
        if target.exists()
        else "absent",
    )
    if planned["status"] != "unchanged":
        moc_files.run(
            config, "Bearer " + client["token"], vid, "apply-file", mid, plan_id=planned["plan_id"]
        )


def mount_channel(config, entry):
    root = config.project_root / "test vault/temp_vault1"
    (root / "a.md").write_text("# 青松\n合成资料压力 2.4 MPa。")
    (root / "unmounted.md").write_text("# 未挂载\n合成资料压力 9.9 MPa，仅未挂载笔记包含这项值。")
    with database.connect(config.state_root) as main:
        vid = main.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
        auth.set_grant(
            main, entry["settings"]["client_id"], vid, query=True, original=False, add=False
        )
        registrar = auth.create_client(main, "Synthetic MOC registrar", False)
        auth.set_grant(main, registrar["id"], vid, query=True, original=False, add=True)
    registered = note_identities.run(
        config,
        "Bearer " + registrar["token"],
        vid,
        "track",
        "a.md",
        hashlib.sha256((root / "a.md").read_bytes()).hexdigest(),
    )
    scope = registrar, vid, root, str(uuid4()), registered["note_identity"]
    moc_associations.run(
        config,
        "Bearer " + registrar["token"],
        vid,
        "create",
        scope[3],
        title="合成分类",
        revision=0,
    )
    moc_associations.run(
        config,
        "Bearer " + registrar["token"],
        vid,
        "link",
        scope[3],
        revision=1,
        owner="system",
        note_identity=scope[4],
        expected_sha256=hashlib.sha256((root / "a.md").read_bytes()).hexdigest(),
    )
    project_moc(config, scope, 2)
    return configure_moc(config, entry, vault_id=vid), scope


@pytest.fixture
def mounted_channel(config, channel):
    return mount_channel(config, channel[0])


def test_scope_defaults_gui_save_and_frozen_messages_are_compatible(config, channel):
    legacy, _ = channel
    with store.readonly(config) as side:
        old = store.get(side, legacy["id"])
    assert "query_note_scope" not in old["value"]
    frozen_legacy = query_scope.snapshot(old)
    assert "content_scope" not in frozen_legacy
    assert query_scope.selected(query_scope.bind(old, None)) == frozen_legacy
    assert (
        frozen_legacy["configuration_sha256"]
        == hashlib.sha256(
            canonical(
                {"revision": old["revision"], "vaults": query_scope.entries(old["value"])}
            ).encode()
        ).hexdigest()
    )

    with pytest.raises(ValidationError):
        configuration.Settings.model_validate({**old["value"], "query_note_scope": "all_notes"})
    with database.connect(config.state_root) as main:
        weixin = configuration.save(
            config,
            main,
            configuration.Save(
                settings=configuration.Settings.model_validate(
                    {**old["value"], "platform": "weixin", "app_id": None, "purpose": "query"}
                )
            ),
        )
    assert weixin["settings"]["query_note_scope"] == "moc_members"

    saved = configure_moc(config, legacy)
    with store.opened(config) as side:
        current = store.get(side, saved["id"])
        side.execute("UPDATE channels SET state='paused' WHERE id=?", (saved["id"],))
    omitted = {k: v for k, v in current["value"].items() if k != "query_note_scope"}
    with database.connect(config.state_root) as main:
        saved = configuration.save(
            config,
            main,
            configuration.Save(
                id=saved["id"],
                expected_revision=saved["revision"],
                settings=configuration.Settings.model_validate(omitted),
            ),
        )
    assert saved["settings"]["query_note_scope"] == "moc_members"

    # A content mode change is rejected even when an old task retained its revision.
    changed = copy.deepcopy(old)
    changed["value"]["query_note_scope"] = "moc_members"
    frozen_moc = query_scope.snapshot(changed)
    assert frozen_moc["content_scope"] == "moc_members"
    assert frozen_moc["configuration_sha256"] != frozen_legacy["configuration_sha256"]
    with pytest.raises(InvalidInput, match="scope_changed"):
        query_scope.bind(changed, frozen_legacy)
    with pytest.raises(InvalidInput, match="scope_changed"):
        query_scope.bind(old, frozen_moc)
    with pytest.raises(InvalidInput, match="scope_changed"):
        query_scope.bind(changed, None)


@pytest.mark.parametrize("index_state", ["absent", "partial", "corrupt"])
def test_gui_and_natural_queries_skip_whole_vault_index_and_unmounted_notes(
    config, mounted_channel, index_state
):
    entry, scope = mounted_channel
    index = note_search_cache.location(config, scope[1])
    index.parent.mkdir(parents=True, exist_ok=True)
    if index_state == "partial":
        with sqlite3.connect(index) as cache:
            cache.executescript(note_search_cache.SCHEMA)
            cache.execute("PRAGMA user_version=1")
            cache.execute("INSERT INTO generation VALUES(?,?,?)", ("old-whole-vault", "partial", 0))
    elif index_state == "corrupt":
        index.write_bytes(b"synthetic corrupt whole-Vault index")
    index_before = index.read_bytes() if index.exists() else None
    notes_before = {p.name: p.read_bytes() for p in scope[2].glob("*.md")}

    with database.connect(config.state_root) as main, store.readonly(config) as side:
        current = store.get(side, entry["id"])
        natural = natural_query.evidence(
            config, main, current, "压力的结论是什么？", ["根本不存在的主题", "压力"]
        )
        assert natural["sources"]
        assert {s["note_path"] for s in natural["sources"]} == {"a.md"}
        assert "压力" in natural["retrieval_queries"]
        assert "9.9" not in json.dumps(natural, ensure_ascii=False)
        assert natural["query_scope"]["content_scope"] == "moc_members"
        query.recheck(config, main, current, natural)

        gui = query.test(config, main, entry["id"], "压力", False)["result"]
        assert {s["note_path"] for s in gui["sources"]} == {"a.md"}
        assert gui["model_used"] is False
        assert "9.9" not in json.dumps(gui, ensure_ascii=False)
    assert (index.read_bytes() if index.exists() else None) == index_before
    assert {p.name: p.read_bytes() for p in scope[2].glob("*.md")} == notes_before


def test_unrelated_invalid_intake_metadata_does_not_block_moc_queries(config, mounted_channel):
    entry, scope = mounted_channel
    bad_metadata = canonical({"published_hashes": False})
    with database.connect(config.state_root) as main, store.readonly(config) as side:
        main.execute(
            "INSERT INTO publication_locations "
            "(kind,publication_id,vault_id,entry_note_path,package_root,revision,state,value) "
            "VALUES('intake','synthetic-unrelated',?,'unrelated.md',NULL,1,'published',?)",
            (scope[1], bad_metadata),
        )
        current = store.get(side, entry["id"])
        evidence = natural_query.evidence(config, main, current, "请介绍压力", ["压力"])
        assert {s["note_path"] for s in evidence["sources"]} == {"a.md"}
        query.recheck(config, main, current, evidence)
        gui = query.test(config, main, entry["id"], "压力", False)["result"]
        assert {s["note_path"] for s in gui["sources"]} == {"a.md"}
        assert (
            main.execute(
                "SELECT value FROM publication_locations WHERE publication_id='synthetic-unrelated'"
            ).fetchone()[0]
            == bad_metadata
        )


@pytest.mark.parametrize("withdrawal", ["unmount", "revoke"])
def test_ready_event_cannot_deliver_after_member_or_permission_withdrawal(
    config, mounted_channel, withdrawal
):
    entry, scope = mounted_channel
    assert dispatch.capture(
        config, entry["id"], entry["revision"], "synthetic-event", "ou_synthetic", "查询：压力"
    )
    dispatch.advance(config, entry["id"], entry["revision"])
    state, event = result(config, entry)
    assert state == "ready" and {s["note_path"] for s in event["result"]["sources"]} == {"a.md"}

    if withdrawal == "unmount":
        moc_associations.run(
            config,
            "Bearer " + scope[0]["token"],
            scope[1],
            "unlink-system",
            scope[3],
            revision=2,
            note_identity=scope[4],
        )
        project_moc(config, scope, 3)
    else:
        with database.connect(config.state_root) as main:
            auth.set_grant(
                main,
                entry["settings"]["client_id"],
                scope[1],
                query=False,
                original=False,
                add=False,
            )

    async def forbidden(*args):
        pytest.fail("withdrawn MOC evidence was sent")

    with pytest.raises((InvalidInput, IntegrityError, auth.Denied)):
        asyncio.run(dispatch.deliver(config, entry["id"], entry["revision"], forbidden))
    assert result(config, entry)[0] != "sent"


def test_empty_moc_collection_stops_before_any_model_call(config, model_channel, monkeypatch):
    entry, _ = model_channel
    entry = configure_moc(config, entry)
    monkeypatch.setattr(
        model_gateway, "complete", lambda *args: pytest.fail("empty MOC collection called a model")
    )
    assert dispatch.capture(
        config, entry["id"], entry["revision"], "empty-moc", "ou_synthetic", "请介绍青松"
    )
    dispatch.advance(config, entry["id"], entry["revision"])
    state, event = result(config, entry)
    assert state == "ready"
    assert event["result"]["sources"] == []
    assert event["result"]["status"] == "insufficient_evidence"
    assert event["result"]["model_used"] is False
    with database.connect(config.state_root) as main:
        assert main.execute("SELECT count(*) FROM model_configuration_calls").fetchone()[0] == 0


def test_missing_mounted_collection_reports_partial_without_model_call(
    config, model_channel, monkeypatch
):
    entry, scope = mount_channel(config, model_channel[0])
    (scope[2] / "a.md").unlink()
    monkeypatch.setattr(
        model_gateway,
        "complete",
        lambda *args: pytest.fail("unavailable MOC members called a model"),
    )
    assert dispatch.capture(
        config, entry["id"], entry["revision"], "missing-moc", "ou_synthetic", "请介绍青松"
    )
    dispatch.advance(config, entry["id"], entry["revision"])
    state, event = result(config, entry)
    response = event["result"]
    assert state == "ready" and response["status"] == "insufficient_evidence"
    assert response["sources"] == [] and response["model_used"] is False
    assert response["coverage_complete"] is False
    assert response["coverage"][scope[1]]["complete"] is False
    assert response["coverage"][scope[1]]["unavailable_count"] == 1
    assert "暂不可用" in response["answer"]
    assert "索引" not in response["answer"]
    with database.connect(config.state_root) as main:
        assert main.execute("SELECT count(*) FROM model_configuration_calls").fetchone()[0] == 0
        gui = query.test(config, main, entry["id"], "青松", False)["result"]
        assert gui["coverage_complete"] is False
        assert gui["sources"] == [] and "暂不可用" in gui["answer"]
        assert "索引" not in gui["answer"]
