"""Real local synthetic directories: preserve identities and crash/recovery boundaries."""

import hashlib
import json
import os
from uuid import uuid4

import pytest
from test_message_channels import channel as channel

from fiber_bundle import auth, database, moc_store, note_identities, search, vault_catalog, vaults
from fiber_bundle.channels import dispatch
from fiber_bundle.channels import store as channels
from fiber_bundle.config import StorageError
from fiber_bundle.inputs import AssetInput
from fiber_bundle.vault_relocation import evidence, execution, files, guard, plan
from fiber_bundle.vault_relocation import store as journal
from fiber_bundle.vault_relocation.errors import RelocationError as SettingsError


@pytest.fixture
def relocation(config, store, tmp_path):
    source = tmp_path / "synthetic-notes"
    source.mkdir()
    (source / "a.md").write_text("# Synthetic\n[[MOC]]\n")
    (source / "duplicate.md").write_bytes((source / "a.md").read_bytes())
    (source / ".obsidian").mkdir()
    (source / ".obsidian/app.json").write_text('{"synthetic":true}')
    (source / "MOC.md").write_text("# 人工 MOC\n[[a]]\n")
    target = tmp_path / "new-home"
    target.mkdir()
    target = target / source.name
    with database.connect(config.state_root) as db:
        p = vault_catalog.preview(
            config,
            db,
            name="synthetic-location",
            root=str(source),
            scope={"include": [""], "exclude": []},
            read_only=False,
        )
        vid = vault_catalog.attach(config, db, p["preview_id"])["vault_id"]
        client = auth.create_client(db, "synthetic-location-owner", False)
        auth.set_grant(db, client["id"], vid, query=True, original=True, add=True)
        vaults.set_default_query(db, vid, True)
    for ordinal in (1, 2):
        store.ingest(
            ingress_scope="synthetic:relocation",
            event_id=f"history-{ordinal}",
            vault="synthetic-location",
            source_key="same-source",
            raw_event=json.dumps({"synthetic": ordinal}).encode(),
            items=(
                AssetInput(f"historical version {ordinal}".encode(), "sample.txt", "text/plain"),
            ),
            session_scope="synthetic:relocation",
            session_id="history",
            retention="knowledge",
        )
    first = note_identities.run(
        config,
        "Bearer " + client["token"],
        vid,
        "track",
        "a.md",
        hashlib.sha256((source / "a.md").read_bytes()).hexdigest(),
    )
    second = note_identities.run(
        config,
        "Bearer " + client["token"],
        vid,
        "track",
        "duplicate.md",
        hashlib.sha256((source / "duplicate.md").read_bytes()).hexdigest(),
    )
    assert first["note_identity"] != second["note_identity"]
    mid = str(uuid4())
    key = files.key(source)
    moc_store.save(
        config,
        vid,
        mid,
        client["id"],
        moc_store.Record(
            "人工 MOC",
            *key,
            1,
            {first["note_identity"]: ["manual", "system"], second["note_identity"]: ["manual"]},
        ),
        "create",
        0,
    )
    return source, target, vid, client, mid


def preview(config, fixture):
    return plan.preview(config, fixture[2], str(fixture[1]))


def finish(config, packet, terminal="complete"):
    for _ in range(12):
        status = execution.step(config, packet["id"])
        if status["state"] == terminal:
            return status
        assert status["state"] != "needs_recovery", status
    pytest.fail(str(status))


def test_real_move_preserves_ids_bytes_metadata_and_manual_moc(config, relocation):
    source, target, vid, client, mid = relocation
    with database.connect(config.state_root) as db:
        before = evidence.preservation(config, db, vid)
        history = [tuple(r) for r in db.execute("SELECT * FROM source_versions ORDER BY id")]
        assert len(history) == 2
        row_before = dict(db.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone())
    files_before = files.inventory(source)
    moc_before = moc_store.load(config, vid, mid)
    packet = preview(config, relocation)
    assert source.exists() and not target.exists() and not guard.paused(config, vid)
    execution.start(config, packet["id"], packet["plan_sha256"])
    with database.connect(config.state_root) as db:
        with pytest.raises(SettingsError, match="relocation_in_progress"):
            auth.grant(db, client["id"], vid, "query")
        other = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
        auth.set_grant(db, client["id"], other, query=True, original=False, add=True)
        auth.grant(db, client["id"], other, "query")
    result = finish(config, packet)
    assert result["checkpoint"]["verified"]
    assert not source.exists() and target.exists()
    assert files.inventory(target) == files_before
    assert moc_store.load(config, vid, mid) == moc_before
    with database.connect(config.state_root) as db:
        assert evidence.preservation(config, db, vid) == before
        assert [
            tuple(r) for r in db.execute("SELECT * FROM source_versions ORDER BY id")
        ] == history
        hits = search.search(config, db, client["id"], "Synthetic", [vid])["results"]
        assert {h["note_id"] for h in hits} == set(moc_before.members)
        current = dict(db.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone())
        assert current == row_before | {"absolute_root": str(target)}
        assert vaults.checked_vault(config, auth.grant(db, client["id"], vid, "query")) == target
    assert execution.start(config, packet["id"], packet["plan_sha256"])["state"] == "complete"
    execution.startup(config)
    assert execution.view(config, packet["id"])["state"] == "complete"


@pytest.mark.parametrize(
    "phase", ["quiescing", "checkpointing", "moving", "switching", "verifying"]
)
@pytest.mark.parametrize("action", ["resume", "restore"])
def test_each_interruption_retains_barrier_and_can_resume_or_restore(
    config, relocation, phase, action
):
    packet = preview(config, relocation)
    execution.start(config, packet["id"], packet["plan_sha256"])
    for _ in range(8):
        if execution.view(config, packet["id"])["state"] == phase:
            break
        execution.step(config, packet["id"])
    execution.startup(config)
    assert guard.paused(config, relocation[2])
    check = execution.recovery_preview(config, packet["id"])
    assert check["recovery"][action], check
    execution.recover(config, packet["id"], action, check["recovery"]["sha256"])
    finish(config, packet, "complete" if action == "resume" else "restored")
    assert not guard.paused(config, relocation[2])
    assert relocation[1 if action == "resume" else 0].is_dir()


def test_move_happened_before_journal_update_does_not_repeat_or_overwrite(
    config, relocation, monkeypatch
):
    packet = preview(config, relocation)
    execution.start(config, packet["id"], packet["plan_sha256"])
    while execution.view(config, packet["id"])["state"] != "moving":
        execution.step(config, packet["id"])
    actual = files.move

    def crash(*args, **kwargs):
        actual(*args, **kwargs)
        raise RuntimeError("synthetic power loss")

    monkeypatch.setattr(files, "move", crash)
    with pytest.raises(RuntimeError):
        execution.step(config, packet["id"])
    assert relocation[1].exists() and not relocation[0].exists()
    monkeypatch.setattr(files, "move", lambda *a, **k: pytest.fail("must not repeat the move"))
    execution.startup(config)
    p = execution.recovery_preview(config, packet["id"])
    execution.recover(config, packet["id"], "resume", p["recovery"]["sha256"])
    finish(config, packet)
    assert execution.view(config, packet["id"])["current_path"] == str(relocation[1])


@pytest.mark.parametrize("conflict", ["target", "content", "parent", "permission"])
def test_stale_or_conflicting_plans_preserve_files(config, relocation, conflict):
    packet = preview(config, relocation)
    source, target, vid, client, _ = relocation
    if conflict == "target":
        target.mkdir()
        (target / "unrelated.md").write_text("do not overwrite")
    elif conflict == "content":
        (source / "a.md").write_text("edited after preflight")
    elif conflict == "parent":
        target.parent.rename(target.parent.with_name("other-parent"))
        target.parent.mkdir()
    else:
        with database.connect(config.state_root) as db:
            auth.set_grant(db, client["id"], vid, query=True, original=False, add=True)
    try:
        execution.start(config, packet["id"], packet["plan_sha256"])
        status = execution.step(config, packet["id"])
        assert status["state"] == "needs_recovery"
    except SettingsError:
        pass
    assert source.is_dir()
    assert (source / "a.md").exists()
    if conflict == "target":
        assert (target / "unrelated.md").read_text() == "do not overwrite"


def test_recovery_refuses_modified_files_and_occupied_original_location(config, relocation):
    packet = preview(config, relocation)
    execution.start(config, packet["id"], packet["plan_sha256"])
    while execution.view(config, packet["id"])["state"] != "switching":
        execution.step(config, packet["id"])
    execution.stop(config, packet["id"], packet["plan_sha256"])
    relocation[0].mkdir()
    (relocation[0] / "user.md").write_text("unrelated")
    p = execution.recovery_preview(config, packet["id"])
    assert p["recovery"]["resume"] and not p["recovery"]["restore"]
    with pytest.raises(SettingsError):
        execution.recover(config, packet["id"], "restore", p["recovery"]["sha256"])
    (relocation[1] / "a.md").write_text("edited")
    p = execution.recovery_preview(config, packet["id"])
    assert not p["recovery"]["resume"] and not p["recovery"]["restore"]
    assert (relocation[0] / "user.md").read_text() == "unrelated"


@pytest.mark.parametrize(
    "case", ["legacy", "nested", "symlink", "hardlink", "scope", "cloud", "absolute"]
)
def test_unsupported_inputs_fail_before_any_move(config, relocation, case):
    source, target, vid, _, _ = relocation
    if case == "legacy":
        with database.connect(config.state_root) as db:
            vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    elif case == "nested":
        target = source / "nested"
    elif case == "symlink":
        (source / "link").symlink_to(source / "a.md")
    elif case == "hardlink":
        os.link(source / "a.md", source / "linked")
    elif case == "scope":
        with database.connect(config.state_root) as db:
            db.execute(
                "UPDATE vaults SET scope_json=? WHERE id=?",
                (json.dumps({"include": [""], "exclude": ["private"]}), vid),
            )
    elif case == "cloud":
        target = target.parent / "CloudStorage" / "new"
        target.parent.mkdir()
    elif case == "absolute":
        (source / "absolute.md").write_text(f"[old](<{source}/a.md>)")
    with pytest.raises((SettingsError, OSError, StorageError)):
        plan.preview(config, vid, str(target))
    assert source.exists() and not target.exists()


def test_stop_and_expiry_are_side_effect_free_before_start(config, relocation):
    packet = preview(config, relocation)
    with database.connect(config.state_root) as db:
        value, state = journal.load(db, packet["id"])
        value["plan"]["expires_at"] = 0
        journal.save(db, packet["id"], value, state)
    with pytest.raises(SettingsError, match="expired"):
        execution.start(config, packet["id"], packet["plan_sha256"])
    assert execution.stop(config, packet["id"], packet["plan_sha256"])["state"] == "cancelled"
    assert relocation[0].exists() and not relocation[1].exists()
    assert not guard.paused(config, relocation[2])


def test_running_and_paused_message_bindings_and_pending_events_survive(config, channel):
    from test_message_channels import bind

    value, source = channel
    running = bind(config, value)
    with database.connect(config.state_root) as db:
        vid = running["value"]["vault_id"]
    target = source.parent / "synthetic-channel-moved"
    packet = plan.preview(config, vid, str(target))
    execution.start(config, packet["id"], packet["plan_sha256"])
    assert dispatch.capture(
        config, running["id"], running["revision"], "during-move", "ou_synthetic", "查询：压力"
    )
    with channels.readonly(config) as db:
        assert channels.get(db, running["id"]) == running
        assert (
            db.execute("SELECT state FROM events WHERE event_id='during-move'").fetchone()[0]
            == "captured"
        )
    finish(config, packet)
    dispatch.advance(config, running["id"], running["revision"])
    with channels.readonly(config) as db:
        assert channels.get(db, running["id"]) == running
        assert (
            db.execute("SELECT state FROM events WHERE event_id='during-move'").fetchone()[0]
            == "ready"
        )


def test_admin_transport_auth_csrf_and_synthetic_move(http, config, relocation):
    source, target, vid, _, _ = relocation
    p = http.post(
        "/admin/vault-relocations/preview", json={"vault_id": vid, "destination": str(target)}
    )
    assert p.status_code == 200, p.text
    packet = p.json()
    assert (
        http.post(
            f"/admin/vault-relocations/{packet['id']}/start",
            json={"plan_sha256": packet["plan_sha256"], "external_writers_stopped": False},
        ).status_code
        == 422
    )
    assert (
        http.post(
            f"/admin/vault-relocations/{packet['id']}/start",
            json={"plan_sha256": packet["plan_sha256"], "external_writers_stopped": True},
        ).status_code
        == 200
    )
    import time

    for _ in range(60):
        current = http.get(f"/admin/vault-relocations/{packet['id']}").json()
        if current["state"] in {"complete", "needs_recovery"}:
            break
        time.sleep(0.1)
    assert current["state"] == "complete", current
    assert target.exists() and not source.exists()


def test_atomic_destination_race_never_overwrites_existing_directory(
    config, relocation, monkeypatch
):
    from fiber_bundle import file_exchange

    p = preview(config, relocation)
    execution.start(config, p["id"], p["plan_sha256"])
    while execution.view(config, p["id"])["state"] != "moving":
        execution.step(config, p["id"])
    original = file_exchange.rename

    def collide(*args, **kwargs):
        relocation[1].mkdir()
        (relocation[1] / "unrelated.md").write_text("never overwrite")
        original(*args, **kwargs)

    monkeypatch.setattr(file_exchange, "rename", collide)
    assert execution.step(config, p["id"])["state"] == "needs_recovery"
    assert relocation[0].exists()
    assert (relocation[1] / "unrelated.md").read_text() == "never overwrite"


def test_corrupt_recovery_copy_prevents_the_first_move(config, relocation):
    p = preview(config, relocation)
    execution.start(config, p["id"], p["plan_sha256"])
    while execution.view(config, p["id"])["state"] != "moving":
        execution.step(config, p["id"])
    checkpoint = execution.view(config, p["id"])["checkpoint"]
    from pathlib import Path

    (Path(checkpoint["path"]) / "files/a.md").write_text("corrupted synthetic recovery copy")
    assert execution.step(config, p["id"])["state"] == "needs_recovery"
    assert relocation[0].exists() and not relocation[1].exists()


def test_existing_paused_message_entrance_is_not_started(config, channel):
    value, source = channel
    with channels.opened(config) as db:
        channels.set_state(db, value["id"], "paused")
        before = channels.get(db, value["id"])
    p = plan.preview(config, before["value"]["vault_id"], str(source.parent / "paused-moved"))
    execution.start(config, p["id"], p["plan_sha256"])
    finish(config, p)
    with channels.readonly(config) as db:
        assert channels.get(db, value["id"]) == before


def test_full_root_preflight_does_not_probe_a_denied_subtree(config, relocation, monkeypatch):
    root, target, vid, _, _ = relocation
    (root / "private").mkdir()
    (root / "private/secret.md").write_text("never read")
    (root / ".knowledge-organizer-policy.json").write_text(
        json.dumps(
            {"schema_version": 1, "deny_paths": [{"path": "private", "deny": ["read", "verify"]}]}
        )
    )
    original = os.stat

    def guarded(path, *args, **kwargs):
        assert "/private/secret.md" not in str(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", guarded)
    with pytest.raises(SettingsError, match="full_scope"):
        plan.preview(config, vid, str(target))


def test_raw_material_can_be_captured_after_directory_move_before_switch(config, relocation):
    from test_feishu_ingress import payload

    from fiber_bundle import feishu_receiver, weixin_inbox
    from fiber_bundle.weixin_events import Binding

    source, target, vid, client, _ = relocation
    binding = Binding("feishu:cli_synthetic", "ou_owner", client["id"], vid)
    feishu_receiver.initialize(config, binding)
    p = preview(config, relocation)
    execution.start(config, p["id"], p["plan_sha256"])
    while execution.view(config, p["id"])["state"] != "switching":
        execution.step(config, p["id"])
    assert not source.exists() and target.exists()
    result = feishu_receiver.capture(config, binding, payload(identity="during-relocation"))
    assert isinstance(result, dict)
    assert len(result["events"]) == 1 and result["dispatched"] is False
    finish(config, p)
    with weixin_inbox.connection(config, binding) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM events WHERE account_id=?", (binding.account_id,)
            ).fetchone()[0]
            == 1
        )


def test_relocation_routes_require_admin_and_csrf(http, config, relocation):
    data = {"vault_id": relocation[2], "destination": str(relocation[1])}
    csrf = http.headers.pop("X-CSRF-Token")
    assert http.post("/admin/vault-relocations/preview", json=data).status_code == 403
    assert relocation[0].exists() and not relocation[1].exists()
    http.headers["X-CSRF-Token"] = csrf
    http.cookies.clear()
    assert http.get("/admin/vault-relocations").status_code == 401
    assert http.post("/admin/vault-relocations/preview", json=data).status_code == 401


def test_location_revision_rejects_old_preview_after_round_trip(config, relocation):
    source, target, vid, *_ = relocation
    old = preview(config, relocation)
    first = preview(config, relocation)
    assert old["location_revision"] == first["location_revision"] == 0
    execution.start(config, first["id"], first["plan_sha256"])
    assert finish(config, first)["location_revision"] == 1
    back = plan.preview(config, vid, str(source))
    execution.start(config, back["id"], back["plan_sha256"])
    assert finish(config, back)["location_revision"] == 2
    assert source.exists() and not target.exists()
    with pytest.raises(SettingsError, match="location_changed"):
        execution.start(config, old["id"], old["plan_sha256"])


def test_owner_commands_queue_while_physical_source_is_between_locations(config, relocation):
    from test_feishu_ingress import payload

    from fiber_bundle import feishu_receiver
    from fiber_bundle.organization import store as organization_store
    from fiber_bundle.weixin_events import Binding

    source, target, vid, client, _ = relocation
    binding = Binding("feishu:cli_synthetic", "ou_owner", client["id"], vid)
    feishu_receiver.initialize(config, binding)
    p = preview(config, relocation)
    execution.start(config, p["id"], p["plan_sha256"])
    while execution.view(config, p["id"])["state"] != "switching":
        execution.step(config, p["id"])
    result = feishu_receiver.capture(
        config, binding, payload(body={"text": "/归类"}, identity="queued-command")
    )
    assert result["state"] == "organization_command_recorded"
    finish(config, p)
    records = organization_store.rows(config, "commands", vid)
    assert len(records) == 1 and records[0]["state"] == "queued"
    assert target.exists() and not source.exists()


def test_valid_but_modified_database_snapshot_prevents_move(config, relocation):
    import sqlite3
    from pathlib import Path

    p = preview(config, relocation)
    execution.start(config, p["id"], p["plan_sha256"])
    while execution.view(config, p["id"])["state"] != "moving":
        execution.step(config, p["id"])
    checkpoint = execution.view(config, p["id"])["checkpoint"]
    with sqlite3.connect(Path(checkpoint["path"]) / "metadata.sqlite3") as db:
        db.execute("UPDATE clients SET name='synthetically changed valid snapshot'")
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    status = execution.step(config, p["id"])
    assert (
        status["state"] == "needs_recovery" and status["reason"] == "relocation_checkpoint_invalid"
    )
    assert relocation[0].exists() and not relocation[1].exists()
