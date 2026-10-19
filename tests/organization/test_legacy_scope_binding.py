import json

from test_jobs import ready as ready

from fiber_bundle import database, vault_save_policy, vault_scope
from fiber_bundle.organization import jobs


def test_legacy_saved_policy_survives_only_implicit_tightening_and_repeated_moc_writes(
    config, ready, monkeypatch
):
    cid, vid, root, moc, note = ready
    with database.connect(config.state_root) as db:
        before = db.execute(
            "SELECT value,revision FROM vault_save_policies WHERE vault_id=?", (vid,)
        ).fetchone()
        assert vault_save_policy.get(config, db, vid)["publication_ready"]
    # Simulate the same built-in denial added to this isolated physical Vault.
    monkeypatch.setattr(vault_scope, "PROTECTED", root / "01-Projects/PROTECTED_PROJECT")
    (root / "Map/光学 MOC.md").write_text("# 光学 MOC\n人工内容保留。\n")
    (root / "Inbox/光学.md").write_text("# 光学笔记\n#光学\n光的传播与折射。\n")
    with database.connect(config.state_root) as db:
        assert vault_save_policy.get(config, db, vid)["publication_ready"]
        done = jobs.run(config, db, jobs.submit(config, db, cid, vid), batch=100)
        assert done["state"] == "completed" and sum(i["changed"] for i in done["items"]) == 2, done
        assert vault_save_policy.get(config, db, vid)["publication_ready"]
        after = db.execute(
            "SELECT value,revision FROM vault_save_policies WHERE vault_id=?", (vid,)
        ).fetchone()
        assert tuple(before) == tuple(after)
    assert not (root / "01-Projects/PROTECTED_PROJECT").exists()


def test_compatibility_still_rejects_user_scope_changes_and_replaced_directory(
    config, ready, monkeypatch
):
    _, vid, root, _, _ = ready
    monkeypatch.setattr(vault_scope, "PROTECTED", root / "01-Projects/PROTECTED_PROJECT")
    with database.connect(config.state_root) as db:
        assert vault_save_policy.get(config, db, vid)["publication_ready"]
        old = db.execute("SELECT scope_json FROM vaults WHERE id=?", (vid,)).fetchone()[0]
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            (json.dumps({"include": [""], "exclude": ["other"]}), vid),
        )
        assert not vault_save_policy.get(config, db, vid)["publication_ready"]
        db.execute("UPDATE vaults SET scope_json=? WHERE id=?", (old, vid))
        (root / "Inbox").rename(root / "Inbox-old")
        (root / "Inbox").mkdir()
        assert not vault_save_policy.get(config, db, vid)["publication_ready"]
