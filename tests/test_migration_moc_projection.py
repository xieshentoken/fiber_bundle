import hashlib
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_migration import migration as migration
from test_note_moves import published as published
from test_note_transfer import linked as linked
from test_note_transfer import prepare, run

from fiber_bundle import moc_associations, moc_files, note_identities


def managed(config, scope, vid, root, mid, *, link=False):
    authorization = "Bearer " + scope["client"]["token"]
    moc_associations.run(config, authorization, vid, "create", mid, title="Human MOC", revision=0)
    revision = 1
    if link:
        moc_associations.run(
            config,
            authorization,
            vid,
            "link",
            mid,
            revision=1,
            note_identity=scope["identity"],
            owner="system",
            expected_sha256=hashlib.sha256((root / "manual.md").read_bytes()).hexdigest(),
        )
        revision = 2
    path = root / "MOC.md"
    path.write_text("# 人工地图\n保留人工段落\n")
    note_identities.run(
        config, authorization, vid, "track", "MOC.md", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    plan = moc_files.run(
        config,
        authorization,
        vid,
        "plan-file",
        mid,
        revision=revision,
        relative="MOC.md",
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    moc_files.run(config, authorization, vid, "apply-file", mid, plan_id=plan["plan_id"])
    return revision


@pytest.mark.parametrize("change", ["none", "manual-body", "delete", "managed-block"])
def test_migration_refreshes_managed_moc_and_preserves_manual_exceptions(config, linked, change):
    source, target = str(uuid4()), str(uuid4())
    managed(config, linked, linked["source"], linked["root"], source, link=True)
    target_revision = managed(config, linked, linked["target"], linked["destination"], target)
    execution = prepare(config, linked, target_moc_id=target, moc_revision=target_revision)
    run(config, linked, "apply", execution_id=execution)
    path = linked["destination"] / "MOC.md"
    if change == "manual-body":
        path.write_bytes(
            path.read_bytes().replace("保留人工段落".encode(), b"Human edited paragraph")
        )
    elif change == "delete":
        path.unlink()
    elif change == "managed-block":
        path.write_bytes(path.read_bytes().replace("系统增量关联".encode(), b"Human changed block"))
    before = path.read_bytes() if path.exists() else None
    result = run(config, linked, "project-mocs", execution_id=execution)
    outcomes = {o["vault_id"]: o for o in result["moc_projections"]}
    assert outcomes[linked["source"]]["status"] == "applied"
    assert "manual.md" not in (linked["root"] / "MOC.md").read_text()
    if change in {"none", "manual-body"}:
        assert outcomes[linked["target"]]["status"] == "applied"
        assert "历史笔记.md" in path.read_text()
        assert before.split(b"<!-- fiber-moc:")[0] == path.read_bytes().split(b"<!-- fiber-moc:")[0]
        again = run(config, linked, "project-mocs", execution_id=execution)
        assert all(o["status"] == "unchanged" for o in again["moc_projections"])
    elif change == "delete":
        assert outcomes[linked["target"]]["status"] == "missing_preserved" and not path.exists()
    else:
        assert outcomes[linked["target"]]["status"] == "conflict_preserved"
        assert path.read_bytes() == before
