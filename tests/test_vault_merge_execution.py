"""Complete encrypted backup gates a real copy/rebind/search/original-permission switch."""

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_vault_merge_plan import fingerprints, prepared

from fiber_bundle import (
    auth,
    backup_bundle,
    database,
    operations,
    search,
    vault_merge_execution,
    vault_merge_plan,
)
from fiber_bundle.config import IntegrityError, InvalidInput


def start(config, article_scope, policy, tmp_path):
    cid, source, target, pid, selection = prepared(config, article_scope, policy)
    with database.connect(config.state_root) as db:
        packet = vault_merge_plan.preflight(config, db, selection)
        frozen = vault_merge_plan.freeze(config, db, selection, packet["plan_sha256"])
    backup = (tmp_path / "full-backup").resolve()
    backup_bundle.create(config, backup, "synthetic-merge-backup-password")
    vault_merge_execution.start(
        config, frozen["id"], packet["plan_sha256"], backup, "synthetic-merge-backup-password"
    )
    return cid, source, target, pid, frozen["id"], packet


def test_merge_keeps_source_bytes_and_target_originals_do_not_require_source_grant(
    config, article_scope, policy, tmp_path
):
    cid, source, target, pid, identity, packet = start(config, article_scope, policy, tmp_path)
    before = fingerprints(config)
    with database.connect(config.state_root) as db:
        with pytest.raises(InvalidInput, match="merge_in_progress"):
            auth.grant(db, cid, target, "query")
    for _ in range(len(packet["files"]) + 20):
        state = vault_merge_execution.advance(config, identity)
        if state["state"] == "complete":
            break
    assert state["state"] == "complete", state
    after = fingerprints(config)
    for path, raw in before.items():
        assert after[path] == raw
    with database.connect(config.state_root) as db:
        with pytest.raises(auth.Denied):
            auth.grant(db, cid, source, "query")
        hit = search.vault_search(config, db, cid, "Synthetic", [target])["results"][0]
        assert hit["note_id"] == "publication:" + pid
        assert hit["originals"]
        original = hit["originals"][0]
        parts = original["url"].split("/")
        raw = operations.original(config, db, cid, parts[3], parts[5], parts[7], 1024 * 1024)
        assert raw
        auth.set_grant(db, cid, target, query=True, original=False, add=True)
        assert not search.vault_search(config, db, cid, "Synthetic", [target])["results"][0][
            "originals"
        ]


def test_bad_backup_never_opens_barrier_or_copies(config, article_scope, policy, tmp_path):
    _, _, _, _, selection = prepared(config, article_scope, policy)
    with database.connect(config.state_root) as db:
        packet = vault_merge_plan.preflight(config, db, selection)
        frozen = vault_merge_plan.freeze(config, db, selection, packet["plan_sha256"])
    backup = (tmp_path / "full-backup").resolve()
    backup_bundle.create(config, backup, "synthetic-merge-backup-password")
    before = fingerprints(config)
    with pytest.raises(InvalidInput):
        vault_merge_execution.start(
            config, frozen["id"], packet["plan_sha256"], backup, "wrong-password"
        )
    assert fingerprints(config) == before
    assert vault_merge_execution.view(config, frozen["id"])["state"] == "prepared"


def finish(config, identity, packet):
    for _ in range(len(packet["files"]) + 30):
        state = vault_merge_execution.advance(config, identity)
        if state["state"] == "complete":
            return state
    pytest.fail(str(state))


@pytest.mark.parametrize(
    "phase", ["pausing", "copying", "originals", "rebinding", "switching", "index"]
)
def test_restart_requires_explicit_reconciliation_without_duplicate_files(
    config, article_scope, policy, tmp_path, phase
):
    from fiber_bundle import vault_merge_recovery

    cid, source, target, pid, identity, packet = start(config, article_scope, policy, tmp_path)
    for _ in range(len(packet["files"]) + 20):
        if vault_merge_execution.view(config, identity)["state"] == phase:
            break
        vault_merge_execution.advance(config, identity)
    assert vault_merge_execution.view(config, identity)["state"] == phase
    before = fingerprints(config)
    vault_merge_recovery.startup(config)
    assert vault_merge_execution.view(config, identity)["state"] == "reconcile"
    assert not vault_merge_recovery.tick(config)
    assert fingerprints(config) == before
    vault_merge_recovery.resume(config, identity, packet["plan_sha256"])
    finish(config, identity, packet)
    with database.connect(config.state_root) as db:
        assert len(search.vault_search(config, db, cid, "Synthetic", [target])["results"]) == 1
        assert db.execute(
            "SELECT count(*) FROM receipts WHERE ingress_scope='vault-merge'"
        ).fetchone()[0] == len(packet["inventories"][0]["sources"])


@pytest.mark.parametrize(
    "phase", ["copying", "originals", "rebinding", "switching", "index", "complete"]
)
def test_cancel_and_rollback_preserve_source_and_original_audits(
    config, article_scope, policy, tmp_path, phase
):
    from fiber_bundle import vault_merge_recovery

    cid, source, target, pid, identity, packet = start(config, article_scope, policy, tmp_path)
    before = fingerprints(config)
    for _ in range(len(packet["files"]) + 20):
        if vault_merge_execution.view(config, identity)["state"] == phase:
            break
        vault_merge_execution.advance(config, identity)
    assert vault_merge_execution.view(config, identity)["state"] == phase
    if phase != "complete":
        vault_merge_recovery.cancel(config, identity, packet["plan_sha256"])
        assert not vault_merge_recovery.tick(config)
    state = vault_merge_recovery.rollback(config, identity, packet["plan_sha256"])
    assert state["state"] == "rolled_back"
    assert fingerprints(config) == before
    with database.connect(config.state_root) as db:
        assert auth.grant(db, cid, source, "query")
        assert (
            db.execute("SELECT state FROM article_publications WHERE id=?", (pid,)).fetchone()[0]
            == "published"
        )
        assert not operations.versions(config, db, cid, target, 0, 100)
        # Existing on-disk indexes are invalidated rather than serving stale target copies.
        from fiber_bundle.note_search_build import build
    build(config, cid, source)
    build(config, cid, target)
    with database.connect(config.state_root) as db:
        hit = search.vault_search(config, db, cid, "Synthetic", [source])["results"][0]
        assert hit["source_kind"] == "published_article"
        assert hit["source_url"] == "https://x.com/synthetic/status/123456789"
        assert hit["originals"]
        assert not search.vault_search(config, db, cid, "Synthetic", [target])["results"]


def test_edited_copy_blocks_entire_rollback(config, article_scope, policy, tmp_path):
    from pathlib import Path

    from fiber_bundle import vault_merge_recovery

    *_, identity, packet = start(config, article_scope, policy, tmp_path)
    vault_merge_execution.advance(config, identity)
    vault_merge_execution.advance(config, identity)
    item = packet["files"][0]
    copied = Path(packet["inventories"][-1]["context"]["root"]) / item["target_path"]
    copied.write_bytes(b"user edit")
    before = fingerprints(config)
    vault_merge_recovery.cancel(config, identity, packet["plan_sha256"])
    with pytest.raises(IntegrityError, match="owned_file_edited"):
        vault_merge_recovery.rollback(config, identity, packet["plan_sha256"])
    assert fingerprints(config) == before
    assert vault_merge_execution.view(config, identity)["state"] == "reconcile"


def test_interrupted_partial_copy_can_explicitly_resume(
    config, article_scope, policy, tmp_path, monkeypatch
):
    from fiber_bundle import vault_merge_files, vault_merge_recovery

    *_, identity, packet = start(config, article_scope, policy, tmp_path)
    vault_merge_execution.advance(config, identity)
    original = vault_merge_files.projected

    def fault(*args):
        raise OSError("synthetic interruption after claim")

    monkeypatch.setattr(vault_merge_files, "projected", fault)
    with pytest.raises(OSError):
        vault_merge_execution.advance(config, identity)
    assert vault_merge_execution.view(config, identity)["state"] == "reconcile"
    monkeypatch.setattr(vault_merge_files, "projected", original)
    vault_merge_recovery.resume(config, identity, packet["plan_sha256"])
    finish(config, identity, packet)


@pytest.mark.parametrize("change", ["file", "grant"])
def test_index_time_change_cannot_commit_merge(
    config, article_scope, policy, tmp_path, monkeypatch, change
):
    from pathlib import Path

    from fiber_bundle import note_search_build

    cid, source, target, pid, identity, packet = start(config, article_scope, policy, tmp_path)
    while vault_merge_execution.view(config, identity)["state"] != "index":
        vault_merge_execution.advance(config, identity)
    original = note_search_build.build

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "file":
            file = packet["files"][0]
            (Path(packet["inventories"][-1]["context"]["root"]) / file["target_path"]).write_bytes(
                b"external edit"
            )
        else:
            with database.connect(config.state_root) as db:
                auth.set_grant(db, cid, target, query=True, original=False, add=True)
        return result

    monkeypatch.setattr(note_search_build, "build", changed)
    with pytest.raises(IntegrityError if change == "file" else InvalidInput):
        vault_merge_execution.advance(config, identity)
    assert vault_merge_execution.view(config, identity)["state"] == "reconcile"


@pytest.mark.parametrize("change", [False, True])
def test_rollback_retry_preserves_restored_grants_and_refuses_new_changes(
    config, article_scope, policy, tmp_path, monkeypatch, change
):
    from fiber_bundle import vault_merge_recovery, vault_merge_rollback

    cid, source, target, pid, identity, packet = start(config, article_scope, policy, tmp_path)
    finish(config, identity, packet)
    original = vault_merge_rollback.entrances

    def crash(*args):
        original(*args)
        raise OSError("synthetic crash after restored permissions")

    monkeypatch.setattr(vault_merge_rollback, "entrances", crash)
    with pytest.raises(OSError):
        vault_merge_recovery.rollback(config, identity, packet["plan_sha256"])
    before = fingerprints(config)
    monkeypatch.setattr(vault_merge_rollback, "entrances", original)
    if change:
        with database.connect(config.state_root) as db:
            auth.set_grant(db, cid, source, query=False, original=False, add=False)
        with pytest.raises(IntegrityError, match="permission_changed"):
            vault_merge_recovery.rollback(config, identity, packet["plan_sha256"])
        assert fingerprints(config) == before
    else:
        assert (
            vault_merge_recovery.rollback(config, identity, packet["plan_sha256"])["state"]
            == "rolled_back"
        )
