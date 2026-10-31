"""Explicit download requests and targeted index recovery never replay unknown effects."""

import time
from unittest.mock import Mock

import pytest
from test_jobs import ready as ready

from fiber_bundle import database, vault_scope
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import cloud_downloads, cloud_recovery
from fiber_bundle.vault_sync import recheck, store


def test_unknown_download_is_not_reissued_even_after_restart(config, ready):
    _, vid, _, _, _ = ready
    call = Mock(side_effect=IntegrityError("cloud_recovery_download_unknown"))
    with pytest.raises(IntegrityError):
        cloud_downloads.send(config, vid, "Inbox/a.md", "a" * 64, call)
    result = cloud_downloads.send(config, vid, "Inbox/a.md", "a" * 64, call)
    assert result == {"state": "unknown", "requested_now": False}
    assert call.call_count == 1
    cloud_downloads.observed(config, vid, "Inbox/a.md", "a" * 64, "available")
    # A later explicit download after a new eviction can be sent, not an idle retry.
    call.side_effect = None
    call.return_value = {"state": "requested", "requested_now": True}
    assert cloud_downloads.send(config, vid, "Inbox/a.md", "a" * 64, call)["requested_now"]
    assert call.call_count == 2


def test_explicit_recheck_advances_only_selected_path_preserving_real_event_flags(config, ready):
    cid, vid, root, _, note = ready
    scope = vault_scope.Scope(("",), ("01-Projects/protected-project",), "policy")
    store.register(config, vid, str(root), scope, "binding")
    with store.connection(config, write=True) as db:
        db.execute("UPDATE libraries SET baseline=1 WHERE id=?", (vid,))
        db.execute("DELETE FROM pending")
    now = time.time()
    store.enqueue(config, vid, [("Inbox/传热.md", 4), ("Inbox/other.md", 4)], "9", now=now)
    with store.connection(config, write=True) as db:
        db.execute("UPDATE pending SET due=?,attempts=3", (now + 1800,))
    revision = recheck.request(config, vid, root, "Inbox/传热.md")
    assert recheck.request(config, vid, root, "Inbox/传热.md") == revision
    with store.connection(config) as db:
        rows = {r["path"]: dict(r) for r in db.execute("SELECT * FROM pending")}
        assert rows["Inbox/传热.md"]["due"] < now + 10
        assert rows["Inbox/传热.md"]["flags"] == 4
        assert rows["Inbox/传热.md"]["attempts"] == 3
        assert rows["Inbox/other.md"]["due"] == now + 1800
    assert store.library(config, vid)["cursor"] == "9"
    with pytest.raises(IntegrityError):
        recheck.request(config, vid, root, "01-Projects/protected-project/secret.md")
    recheck.request(config, vid, root, "Inbox/new.md")
    with store.connection(config) as db:
        assert db.execute("SELECT flags FROM pending WHERE path='Inbox/new.md'").fetchone()[0] == 0


def test_recheck_requires_current_available_note_and_permission(config, ready, monkeypatch):
    cid, vid, root, _, note = ready
    native = Mock(return_value={"state": "not_requested", "requested_now": False})
    queue = Mock(return_value=7)
    monkeypatch.setattr(cloud_recovery, "native", native)
    monkeypatch.setattr(recheck, "request", queue)
    data = dict(client_id=cid, vault_id=vid, path="Inbox/传热.md")
    with database.connect(config.state_root) as db:
        checked = cloud_recovery.operate(config, db, cloud_recovery.FileScope(**data))
        with pytest.raises(IntegrityError, match="not_downloaded"):
            cloud_recovery.operate(
                config,
                db,
                cloud_recovery.FileScope(**data, action="recheck", expected=checked["expected"]),
            )
        assert not queue.called
        native.return_value = {"state": "available", "requested_now": False}
        value = cloud_recovery.operate(
            config,
            db,
            cloud_recovery.FileScope(**data, action="recheck", expected=checked["expected"]),
        )
        assert value["index_generation"] == 7 and queue.call_count == 1
        note.write_text("changed")
        with pytest.raises(IntegrityError, match="file_changed"):
            cloud_recovery.operate(
                config,
                db,
                cloud_recovery.FileScope(**data, action="recheck", expected=checked["expected"]),
            )
        assert queue.call_count == 1
