"""Managed MOC journals use current names when resolving human short links."""

from pathlib import Path

import pytest
from test_jobs import ready as ready

from fiber_bundle import database, moc_files, moc_store, moc_write_scope, partial_notes, vault_scope
from fiber_bundle.config import IntegrityError
from fiber_bundle.objects import digest
from fiber_bundle.organization import jobs, note_moc_sync, packages
from fiber_bundle.vault_sync import scanner, store


def plan(config, ready):
    cid, vid, root, moc, _ = ready
    moc.write_text("---\ntype: moc\n---\n# 热管理 MOC\n\n人工链接 [[传热]]。\n")
    with database.connect(config.state_root) as db:
        vault = db.execute("SELECT * FROM vaults WHERE id=?", (vid,)).fetchone()
        scope = vault_scope.for_vault(root, vault)
    store.register(config, vid, str(root), scope, "test-current-scope")
    while tasks := store.due(config, now=10):
        for task in tasks:
            scanner.apply(config, task, now=10)
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        source = next(e for e in entities if e["path"] == "Inbox/传热.md")
        target = mocs[0]
        note_moc_sync.track(config, db, cid, vid, source)
        info = root.stat()
        record = moc_store.Record(
            target["title"], info.st_dev, info.st_ino, 1, {source["id"]: ["system"]}
        )
        moc_store.save(config, vid, target["id"], cid, record, "fixture", 0)
        raw = moc.read_bytes()
        packet = moc_files.plan_file(
            config,
            db,
            cid,
            vid,
            target["id"],
            1,
            target["path"],
            digest(raw),
            read_scope=moc_write_scope.intent(target["path"], record, raw, target["id"]),
        )
    return target, packet, raw


@pytest.mark.parametrize("new_path", ["Resource/传热.md", "Resource/新增/传热.md"])
def test_unobserved_alias_candidate_blocks_managed_plan(config, ready, new_path):
    cid, vid, root, moc, _ = ready
    target, packet, raw = plan(config, ready)
    duplicate = root / new_path
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    duplicate.write_text("# 同名人工资料\n不能按旧名字集合判断短链唯一。\n")
    with database.connect(config.state_root) as db:
        with pytest.raises(IntegrityError, match="moc_write_scope_catalogue_changed"):
            moc_files.apply_file(config, db, cid, vid, target["id"], packet["plan_id"])
        assert moc.read_bytes() == raw
        # Observing the real change does not retroactively authorize the old
        # journal: the extra candidate changes its frozen rendering evidence.
        store.enqueue(config, vid, [("", 1)], "20", now=20)
        while tasks := store.due(config, now=30):
            for task in tasks:
                scanner.apply(config, task, now=30)
        with pytest.raises(IntegrityError, match="sources changed"):
            moc_files.apply_file(config, db, cid, vid, target["id"], packet["plan_id"])
    assert moc.read_bytes() == raw


def test_namespace_change_during_body_observation_cannot_publish_scope(config, ready, monkeypatch):
    cid, vid, root, moc, _ = ready
    target, packet, raw = plan(config, ready)
    original = partial_notes.read
    changed = False

    def read(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            (root / "Resource/传热.md").write_text("# 新的同名资料\n")
            changed = True
        return result

    monkeypatch.setattr(partial_notes, "read", read)
    with database.connect(config.state_root) as db:
        with pytest.raises(IntegrityError, match="moc_write_scope_catalogue_changed"):
            moc_files.apply_file(config, db, cid, vid, target["id"], packet["plan_id"])
    assert moc.read_bytes() == raw


def test_managed_scope_filters_protected_tree_before_metadata_and_allows_own_swap(
    config, ready, monkeypatch
):
    cid, vid, root, moc, _ = ready
    from fiber_bundle import vault_save_policy
    from fiber_bundle.vault_save_contract import SaveSettings

    protected = root / "private"
    protected.mkdir(parents=True)
    (protected / "传热.md").write_text("private protected content")
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            ('{"include":[""],"exclude":["private"]}', vid),
        )
        policy = vault_save_policy.get(config, db, vid)
        preview = vault_save_policy.preview(
            config,
            db,
            vid,
            SaveSettings.model_validate(policy["settings"]),
            policy["revision"],
            enable_publication=True,
        )
        vault_save_policy.save(config, db, vid, preview["preview_id"], policy["revision"])
    target, packet, raw = plan(config, ready)
    original = Path.lstat

    def checked(path, *args, **kwargs):
        assert path != protected and protected not in path.parents
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", checked)
    with database.connect(config.state_root) as db:
        result = moc_files.apply_file(config, db, cid, vid, target["id"], packet["plan_id"])
    assert result["status"] == "applied"
    assert moc.read_bytes().startswith(raw)
    assert b"%E4%BC%A0%E7%83%AD" not in moc.read_bytes()  # Unambiguous human link suffices.


def test_registered_draft_assets_do_not_consume_ordinary_name_budget(
    config, ready, package, monkeypatch
):
    cid, vid, _, moc, _ = ready
    with database.connect(config.state_root) as db:
        packages.register(config, db, cid, vid, "Book/fake-thermal")
    target, packet, raw = plan(config, ready)
    # A large package's observer metadata must be excluded before LIMIT and
    # traversal, including Markdown assets and nested asset directories.
    with store.connection(config, write=True) as side:
        signature = side.execute(
            "SELECT signature FROM files WHERE vault=? AND path='Book/fake-thermal/SKILL.md'",
            (vid,),
        ).fetchone()[0]
        side.executemany(
            "INSERT INTO files VALUES(?,?,?,?,?,?)",
            [
                (vid, f"Book/fake-thermal/assets/bulk-{i}.md", signature, 0, i % 2, 1)
                for i in range(moc_write_scope.MAX_PATHS + 1)
            ],
        )
    original = Path.lstat

    def checked(path, *args, **kwargs):
        assert path != package and package not in path.parents
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", checked)
    with database.connect(config.state_root) as db:
        result = moc_files.apply_file(config, db, cid, vid, target["id"], packet["plan_id"])
    assert result["status"] == "applied"
    assert moc.read_bytes().startswith(raw)
