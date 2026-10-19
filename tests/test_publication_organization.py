import hashlib
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_moves import published as published

from fiber_bundle import auth, moc_associations, moc_dispatch, moc_store, publication_organization
from fiber_bundle import publication_organization_store as intents
from fiber_bundle.config import StorageError


@pytest.fixture
def organization(config, published, article_scope):
    db, result, note, rid = published
    token = "synthetic-local-organization-token"
    db.execute(
        "UPDATE clients SET token_digest=? WHERE id=?", (auth.digest(token), article_scope[0])
    )
    return {
        "authorization": "Bearer " + token,
        "client": article_scope[0],
        "vault": article_scope[1],
        "run": rid,
        "note": note,
        "result": result,
    }


def queue(config, scope, **selection):
    return publication_organization.queue(config, scope["authorization"], scope["run"], **selection)


def test_explicit_new_moc_queue_is_default_off_and_keeps_human_note(config, organization):
    scope = organization
    body = scope["note"].read_bytes()
    selection = dict(
        moc_id=str(uuid4()),
        revision=0,
        relative="New MOC.md",
        expected_sha256="absent",
        new_title="New local MOC",
    )
    job = queue(config, scope, **selection)
    assert job["state"] == "queued" and not (scope["note"].parent.parent / "New MOC.md").exists()
    assert queue(config, scope, **selection) == job
    assert moc_dispatch.dispatch(config, scope["authorization"], scope["vault"])["processed"] == 0
    outcome = moc_dispatch.dispatch(
        config, scope["authorization"], scope["vault"], enabled=True, maximum=1
    )
    assert outcome["processed"] == 1 and outcome["results"][0]["state"] == "applied"
    moc = scope["note"].parent.parent / "New MOC.md"
    assert scope["note"].parent.name in moc.read_text()
    assert scope["note"].read_bytes() == body
    value = intents.load(config, scope["run"], selection["moc_id"])
    assert intents.proved(config, value)
    assert moc_store.load(config, scope["vault"], selection["moc_id"]).members == {
        "publication:" + scope["result"]["publication_id"]: ["system"]
    }


def test_existing_manual_moc_body_and_ownership_preserved(config, organization):
    scope = organization
    mid = str(uuid4())
    moc_associations.run(
        config, scope["authorization"], scope["vault"], "create", mid, title="Human", revision=0
    )
    moc = scope["note"].parent.parent / "Human.md"
    human = b"# Human authored MOC\n\nHuman paragraph.\n"
    moc.write_bytes(human)
    selection = dict(
        moc_id=mid,
        revision=1,
        relative="Human.md",
        expected_sha256=hashlib.sha256(human).hexdigest(),
    )
    queue(config, scope, **selection)
    moc_dispatch.dispatch(config, scope["authorization"], scope["vault"], enabled=True, maximum=1)
    assert moc.read_bytes().startswith(human)
    with pytest.raises(StorageError, match="immutable"):
        queue(config, scope, **{**selection, "relative": "Other.md"})


@pytest.mark.parametrize("boundary", ["save", "queue"])
def test_metadata_failure_recovery_uses_audited_contribution(config, organization, boundary):
    scope = organization
    selection = dict(
        moc_id=str(uuid4()),
        revision=0,
        relative="MOC.md",
        expected_sha256="absent",
        new_title="MOC",
    )
    owner, method = (moc_store, "save") if boundary == "save" else (moc_dispatch, "schedule_locked")
    original = getattr(owner, method)

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic failure after commit")

    with patch.object(owner, method, side_effect=failed), pytest.raises(OSError):
        queue(config, scope, **selection)
    record = moc_store.load(config, scope["vault"], selection["moc_id"])
    assert record.revision == 1
    recovered = queue(config, scope, **selection)
    assert recovered["state"] == "queued"
    assert moc_store.load(config, scope["vault"], selection["moc_id"]).revision == 1


def test_conflicting_or_missing_permission_does_not_add_moc(config, organization):
    scope = organization
    selection = dict(
        moc_id=str(uuid4()),
        revision=0,
        relative="MOC.md",
        expected_sha256="absent",
        new_title="MOC",
    )
    (scope["note"].parent.parent / "MOC.md").write_text("human collision")
    with pytest.raises(StorageError):
        queue(config, scope, **selection)
    assert moc_store.load(config, scope["vault"], selection["moc_id"]) is None


def test_publication_cli_default_off_and_explicit_enqueue(
    config, organization, monkeypatch, capsys
):
    import json
    import sys

    from fiber_bundle import publication_cli

    token = config.project_root / "synthetic-token.txt"
    token.write_text(organization["authorization"].removeprefix("Bearer "))
    base = [
        "fiber-publish",
        "--project-root",
        str(config.project_root),
        "--config",
        "config.json",
        "--token-file",
        str(token),
        "--run-id",
        organization["run"],
    ]
    monkeypatch.setattr(sys, "argv", base)
    assert publication_cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["published"] and "organization" not in result
    mid = str(uuid4())
    monkeypatch.setattr(
        sys,
        "argv",
        base
        + [
            "--organize",
            "--moc-id",
            mid,
            "--moc-revision",
            "0",
            "--moc-path",
            "New.md",
            "--moc-expected-sha256",
            "absent",
            "--new-moc-title",
            "New",
        ],
    )
    assert publication_cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["published"] and result["organization"]["state"] == "queued"
    assert not (organization["note"].parent.parent / "New.md").exists()
