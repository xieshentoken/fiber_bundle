import hashlib
import json
import sqlite3
import sys
from dataclasses import replace
from uuid import uuid4

import pytest
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_identities import track

from fiber_bundle import (
    auth,
    backup_bundle,
    backup_cli,
    backup_preflight,
    backup_restore,
    database,
    moc_associations,
    restore_guard,
    vault_catalog,
    vaults,
)
from fiber_bundle.backup_crypto import CHUNK, Records
from fiber_bundle.config import IntegrityError, InvalidInput, load_config

PASSWORD = "synthetic-backup-password-only"


def restored(tmp_path):
    return load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")


def test_complete_encrypted_roundtrip_preserves_bytes_and_disables_imported_access(
    config, store, request_data, tmp_path
):
    receipt = store.ingest(**request_data)
    auth.set_password(config, "synthetic-admin-password")
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic-backup", False)
    auth.login(db, "synthetic-admin-password", 3600)
    grants = db.execute("SELECT * FROM client_vault_grants").fetchall()
    db.close()
    note = config.project_root / dict(config.vaults)["personal"] / "note.md"
    note.write_text("private-vault-content-sentinel")
    config_dir = config.project_root / "configs"
    config_dir.mkdir()
    (config_dir / "worker.json").write_text('{"synthetic":true}')
    before = (config.state_root / "metadata.sqlite3").read_bytes()
    marker = backup_bundle.create(
        config, tmp_path / "bundle", PASSWORD, include_configs=("configs/worker.json",)
    )
    assert set(p.name for p in (tmp_path / "bundle").iterdir()) == {
        "state.fbenc",
        "vaults.fbenc",
        "bundle.json",
    }
    for p in (tmp_path / "bundle").iterdir():
        assert p.stat().st_mode & 0o777 == 0o600
        assert client["token"].encode() not in p.read_bytes()
        assert b"private-vault-content-sentinel" not in p.read_bytes()
        assert PASSWORD.encode() not in p.read_bytes()
    assert (config.state_root / "metadata.sqlite3").read_bytes() == before
    result = backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    assert result["original_hashes_verified"] and result["associations_verified"]
    assert result["state"] == "restored_locked" and not result["automatic_start"]
    target = restored(tmp_path)
    assert (
        target.vault_path(dict(target.vaults)["personal"]).joinpath("note.md").read_bytes()
        == note.read_bytes()
    )
    assert (target.project_root / "configs/worker.json").read_text() == '{"synthetic":true}'
    with pytest.raises(InvalidInput, match="restore_requires"):
        database.connect(target.state_root)
    backup_restore.activate(target, source_stopped=True)
    db = database.connect(target.state_root)
    assert db.execute("SELECT * FROM client_vault_grants").fetchall() == grants
    assert db.execute("SELECT count(*) FROM admin_sessions").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM admin_auth").fetchone()[0] == 0
    assert db.execute("SELECT active FROM clients").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()
    assert (
        __import__("fiber_bundle.store", fromlist=["Store"])
        .Store(target)
        .receipt(receipt.job_id)
        .state
        == "stored"
    )
    assert marker["external_secrets"] == "reconfigure"


@pytest.mark.parametrize("change", ["moved", "edited"])
def test_stable_note_identity_rebound_with_original_history_preserved(
    config, identity_scope, tmp_path, change
):
    tracked = track(config, identity_scope)
    path = identity_scope[2] / "manual.md"
    if change == "moved":
        path.rename(path.with_name("renamed.md"))
    else:
        path.write_text("# user edit after registration")
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    target = restored(tmp_path)
    import sqlite3

    db = sqlite3.connect(target.state_root / "note-identities.sqlite3")
    row = db.execute(
        "SELECT identity,relative_path,revision,file_ino FROM registrations"
    ).fetchone()
    assert row[0] == tracked["note_identity"]
    assert row[1] == ("renamed.md" if change == "moved" else "manual.md")
    assert row[2] == 2
    assert (
        row[3] == target.vault_path(dict(target.vaults)["personal"]).joinpath(row[1]).stat().st_ino
    )
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    db.close()


def test_generated_pending_vault_rebinds_and_does_not_grant_access(config, store, tmp_path):
    db = database.connect(config.state_root)
    client = auth.create_client(db, "creator", True)
    vid = vaults.reserve(config, db, client["id"], "pending-vault")
    db.close()
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    target = restored(tmp_path)
    assert any(
        v["id"] == vid and v["status"] == "pending"
        for v in backup_preflight.inspect(target)["vaults"]
    )


@pytest.mark.parametrize("attachment_state", ["detached", "trashed"])
def test_excluded_vault_identity_and_moc_history_restore_without_reading_old_root(
    config, identity_scope, tmp_path, attachment_state
):
    client, vid, root = identity_scope
    first = track(config, identity_scope)
    (root / "second.md").write_text("# Another historical identity")
    second = track(config, identity_scope, "second.md")
    mid = str(uuid4())
    authorization = "Bearer " + client["token"]
    moc_associations.run(
        config, authorization, vid, "create", mid, title="Historical MOC", revision=0
    )
    moc_associations.run(
        config,
        authorization,
        vid,
        "link",
        mid,
        revision=1,
        note_identity=first["note_identity"],
        owner="manual",
        expected_sha256=hashlib.sha256((root / "manual.md").read_bytes()).hexdigest(),
    )
    db = database.connect(config.state_root)
    vault_catalog.unlink(config, db, vid)
    if attachment_state == "trashed":
        db.execute("UPDATE vaults SET attachment_state='trashed' WHERE id=?", (vid,))
        db.commit()
    db.close()
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    # Detached content is outside backup ownership. Restoration cannot depend on it.
    root.rename(root.with_name("original-outside-backup"))
    result = backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    assert result["state"] == "restored_locked" and not result["automatic_start"]
    target = restored(tmp_path)
    with sqlite3.connect(target.state_root / "note-identities.sqlite3") as side:
        rows = side.execute(
            "SELECT identity,vault_dev,vault_ino,revision FROM registrations WHERE vault_id=?",
            (vid,),
        ).fetchall()
        assert {row[0] for row in rows} == {first["note_identity"], second["note_identity"]}
        assert all(row[1:] == (0, 0, 2) for row in rows)
        assert side.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    with sqlite3.connect(target.state_root / "moc-associations.sqlite3") as side:
        record = json.loads(side.execute("SELECT record_json FROM mocs").fetchone()[0])
        assert (record["vault_dev"], record["vault_ino"]) == (0, 0)
        assert record["members"] == {first["note_identity"]: ["manual"]}
        assert side.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    audit = json.loads((target.state_root / "restore-rebind.json").read_text())
    assert (
        sum(
            row.get("state") == "vault_not_restored_manual_rebind_required"
            for row in audit["rebindings"]
        )
        == 2
    )
    backup_restore.activate(target, source_stopped=True)
    db = database.connect(target.state_root)
    vault = db.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone()
    assert vault["attachment_state"] == attachment_state
    with pytest.raises(auth.Denied):
        vaults.checked_vault(target, vault)
    db.close()


def test_unknown_vault_identity_in_authenticated_backup_still_blocks_restore(
    config, identity_scope, tmp_path
):
    track(config, identity_scope)
    with sqlite3.connect(config.state_root / "note-identities.sqlite3") as side:
        side.execute("UPDATE registrations SET vault_id=?", (str(uuid4()),))
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    with pytest.raises(IntegrityError, match="restore_identity_vault_missing"):
        backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    assert restore_guard.read(tmp_path / "restored/.state")["state"] == "failed"


@pytest.mark.parametrize("part", ["state.fbenc", "vaults.fbenc"])
@pytest.mark.parametrize("damage", ["flip", "truncate", "append"])
def test_authenticated_parts_reject_corruption_even_if_public_checksums_changed(
    config, store, tmp_path, part, damage
):
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    path = tmp_path / "bundle" / part
    data = path.read_bytes()
    if damage == "flip":
        data = data[:-1] + bytes([data[-1] ^ 1])
    elif damage == "truncate":
        data = data[:-5]
    else:
        data += b"extra"
    path.write_bytes(data)
    p = tmp_path / "bundle/bundle.json"
    marker = json.loads(p.read_text())
    marker["files"][part] = hashlib.sha256(data).hexdigest()
    p.write_text(json.dumps(marker))
    with pytest.raises(IntegrityError):
        backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    if (tmp_path / "restored/.state/restore-gate.json").exists():
        assert restore_guard.read(tmp_path / "restored/.state")["state"] == "failed"


def test_wrong_password_never_creates_destination(config, store, tmp_path):
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    with pytest.raises(IntegrityError, match="authentication"):
        backup_restore.restore(
            tmp_path / "bundle", tmp_path / "restored", "wrong-password-long-enough"
        )
    assert not (tmp_path / "restored").exists()


def test_backup_creation_mutation_keeps_incomplete_bundle_unpublished(
    config, store, tmp_path, monkeypatch
):
    path = config.vault_path(dict(config.vaults)["personal"]) / "note.md"
    path.write_bytes(b"a" * (CHUNK + 3))
    put = Records.put
    changed = False

    def modifying(self, value):
        nonlocal changed
        put(self, value)
        if value.startswith(b"a" * 10) and not changed:
            changed = True
            path.write_bytes(b"changed")

    monkeypatch.setattr(Records, "put", modifying)
    with pytest.raises(IntegrityError):
        backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    assert not (tmp_path / "bundle/bundle.json").exists()


@pytest.mark.parametrize("operation", ["backup", "restore"])
def test_destinations_exclusive_and_source_not_overwritten(config, store, tmp_path, operation):
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "sentinel").write_bytes(b"keep")
    with pytest.raises(InvalidInput):
        if operation == "backup":
            backup_bundle.create(config, existing, PASSWORD)
        else:
            backup_restore.restore(tmp_path / "bundle", existing, PASSWORD)
    assert (existing / "sentinel").read_bytes() == b"keep"
    with pytest.raises(InvalidInput, match="overlaps"):
        backup_bundle.create(config, config.state_root / "backup", PASSWORD)


def test_activation_requires_operator_ack_and_host_binding(config, store, tmp_path, monkeypatch):
    backup_bundle.create(config, tmp_path / "bundle", PASSWORD)
    backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    target = restored(tmp_path)
    with pytest.raises(InvalidInput):
        backup_restore.activate(target, source_stopped=False)
    backup_restore.activate(target, source_stopped=True)
    monkeypatch.setattr(restore_guard, "machine_id", lambda: "different-host")
    with pytest.raises(InvalidInput):
        database.connect(target.state_root)


def test_retired_source_cannot_restart(config, store):
    assert backup_restore.retire(config)["state"] == "retired"
    with pytest.raises(InvalidInput):
        database.connect(config.state_root)
    assert backup_preflight.inspect(config)["state"] == "PASS"


@pytest.mark.parametrize("value", ["short", "x" * 1025])
def test_password_limits(config, store, tmp_path, value):
    with pytest.raises(InvalidInput, match="password_length"):
        backup_bundle.create(config, tmp_path / "bundle", value)
    assert not (tmp_path / "bundle/bundle.json").exists()


def test_cli_interactive_secret_does_not_appear_in_output(
    config, store, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(backup_cli.getpass, "getpass", lambda _: PASSWORD)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fiber-backup",
            "create",
            "--project-root",
            str(config.project_root),
            "--config",
            "config.json",
            "--destination",
            str(tmp_path / "bundle"),
        ],
    )
    assert backup_cli.main() == 0
    assert PASSWORD not in capsys.readouterr().out
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fiber-backup",
            "restore",
            "--bundle",
            str(tmp_path / "bundle"),
            "--destination",
            str(tmp_path / "restored"),
        ],
    )
    assert backup_cli.main() == 0
    assert PASSWORD not in capsys.readouterr().out


def test_explicit_external_vault_keeps_state_local_and_restore_uses_local_copy(
    config, store, tmp_path
):
    external = tmp_path / "external"
    external.mkdir()
    (external / "icloud-note.md").write_text("local-visible-only")
    mapped = replace(config, vault_roots=(("personal", str(external)),))
    mapped.validate()
    backup_bundle.create(mapped, tmp_path / "bundle", PASSWORD)
    backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", PASSWORD)
    target = restored(tmp_path)
    assert not target.vault_roots
    assert target.vault_path(dict(target.vaults)["personal"]).joinpath("icloud-note.md").exists()
    assert external.joinpath("icloud-note.md").read_text() == "local-visible-only"


@pytest.mark.parametrize("relative", ["../secret", "objects/key", "configs/../secret"])
def test_extra_configuration_path_rejected(config, store, tmp_path, relative):
    with pytest.raises(InvalidInput):
        backup_bundle.create(config, tmp_path / "bundle", PASSWORD, include_configs=(relative,))
