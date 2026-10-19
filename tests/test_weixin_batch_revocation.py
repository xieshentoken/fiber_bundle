import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
import test_weixin_task_inputs as task_inputs
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_weixin_batches import at, capture
from test_weixin_inbox import binding as binding
from test_weixin_publication import articles, complete, note

from fiber_bundle import (
    auth,
    database,
    file_exchange,
    moc_associations,
    search,
    weixin_batches,
    weixin_publication,
    weixin_publication_dispatch,
)
from fiber_bundle import (
    weixin_batch_revocation as revocation,
)
from fiber_bundle.config import StorageError


@pytest.fixture
def published(config, binding, policy):
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, query=True, add=True, original=False)
    db.close()
    with (
        patch.object(
            task_inputs,
            "capture",
            side_effect=lambda c, b, e, t, **kw: capture(c, b, "second-" + e, t + 10, **kw),
        ),
        patch.object(task_inputs, "at", side_effect=lambda t: at(t + 10)),
    ):
        batch, runs = articles(config, binding, policy)
    complete(config, policy, runs[0])
    weixin_publication.plan(config, binding, batch)
    result = weixin_publication_dispatch.dispatch(config, binding, maximum=1)["results"][0]
    return batch, runs[0], result


def cancel(config, binding, published):
    weixin_batches.choose(config, binding, published[0], "cancel")


def test_revoke_only_new_note_retains_originals_attachments_and_history(config, binding, published):
    batch, _, result = published
    source = note(config, result)
    before = {p: p.read_bytes() for p in source.parent.rglob("*") if p.is_file() and p != source}
    main = (config.state_root / "metadata.sqlite3").read_bytes()
    with pytest.raises(StorageError):
        revocation.run(config, binding, batch)
    cancel(config, binding, published)
    plan = revocation.run(config, binding, batch)
    assert plan["results"][0]["state"] == "planned" and source.exists()
    output = revocation.run(config, binding, batch, "apply")
    assert output["results"][0]["state"] == "revoked" and not source.exists()
    assert not output["external_effects_reversed"]
    assert before == {p: p.read_bytes() for p in before}
    assert (config.state_root / "metadata.sqlite3").read_bytes() == main
    assert list(source.parent.glob(".fiber-revoked-*.md"))[0].is_file()
    assert revocation.run(config, binding, batch, "apply") == output
    db = database.connect(config.state_root)
    assert (
        search.search(config, db, binding.client_id, "synthetic", [binding.vault_id])["results"]
        == []
    )
    db.close()
    with pytest.raises((StorageError, auth.Denied)):
        weixin_publication.reconcile(config, binding, published[1])
    assert not source.exists()


@pytest.mark.parametrize(
    "change", ["edit", "same-content-replacement", "delete", "association", "missing-origin"]
)
@pytest.mark.parametrize("when", ["before_plan", "after_plan"])
def test_human_or_shared_changes_are_exceptions_not_deleted(
    config, binding, published, change, when
):
    batch, _, result = published
    source = note(config, result)
    cancel(config, binding, published)
    if when == "after_plan":
        revocation.run(config, binding, batch)
    if change == "edit":
        source.write_text("Human edited body")
    elif change == "same-content-replacement":
        replacement = source.with_name("copy.tmp")
        replacement.write_bytes(source.read_bytes())
        replacement.replace(source)
    elif change == "delete":
        source.unlink()
    elif change == "missing-origin":
        (config.state_root / "publication-origins" / (result["publication_id"] + ".json")).unlink()
    else:
        mid = str(uuid4())
        db = database.connect(config.state_root)
        token = db.execute("SELECT id FROM clients WHERE id=?", (binding.client_id,)).fetchone()
        db.close()
        assert token
        # Trusted metadata API normally authenticates by token. Use the existing binding's
        # client check without inventing a second identity in this local fixture.
        with patch.object(auth, "client", return_value={"id": binding.client_id}):
            moc_associations.run(
                config,
                "Bearer synthetic",
                binding.vault_id,
                "create",
                mid,
                title="Human ownership",
                revision=0,
            )
            moc_associations.run(
                config,
                "Bearer synthetic",
                binding.vault_id,
                "link",
                mid,
                revision=1,
                note_identity="publication:" + result["publication_id"],
                owner="manual",
                expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            )
    if when == "before_plan":
        revocation.run(config, binding, batch)
    result = revocation.run(config, binding, batch, "apply")
    assert result["results"][0]["state"] in {
        "human_modified_or_replaced",
        "already_missing",
        "shared_or_associated",
        "existing_or_unproven",
    }
    assert source.exists() == (change != "delete")
    if change == "edit":
        assert source.read_text() == "Human edited body"


def test_reused_publication_owned_by_another_batch_is_never_revoked(
    config, binding, policy, published
):
    batch, runs = articles(config, binding, policy)
    complete(config, policy, runs[0])
    weixin_publication.plan(config, binding, batch)
    result = weixin_publication_dispatch.dispatch(config, binding, maximum=1)["results"][0]
    assert result["publication_id"] == published[2]["publication_id"]
    weixin_batches.choose(config, binding, batch, "cancel")
    assert revocation.run(config, binding, batch)["results"][0]["state"] == "existing_or_unproven"
    assert (
        revocation.run(config, binding, batch, "apply")["results"][0]["state"]
        == "existing_or_unproven"
    )
    assert note(config, result).exists()
    cancel(config, binding, published)
    assert (
        revocation.run(config, binding, published[0])["results"][0]["state"]
        == "shared_or_associated"
    )


def test_wrong_binding_and_permissions_cannot_read_or_revoke(config, binding, published):
    cancel(config, binding, published)
    with pytest.raises(StorageError):
        revocation.run(config, replace(binding, owner_id="other-owner"), published[0])
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, query=False, add=True, original=False)
    db.close()
    with patch.object(
        weixin_batches, "snapshot", side_effect=AssertionError("unauthorized batch read")
    ):
        with pytest.raises(auth.Denied):
            revocation.run(config, binding, published[0])


def test_real_exit_after_hide_requires_explicit_reconciliation_no_replay(
    config, binding, published
):
    cancel(config, binding, published)
    revocation.run(config, binding, published[0])
    values = [binding.account_id, binding.owner_id, binding.client_id, binding.vault_id]
    script = """
import os,sys,json
from pathlib import Path
from fiber_bundle.config import load_config
from fiber_bundle.weixin_events import Binding
from fiber_bundle import file_exchange, weixin_batch_revocation as revocation
root,batch,raw=sys.argv[1:]
config=load_config(Path(root)/'config.json',Path(root))
original=file_exchange.rename
def crash(*a,**kw):
    original(*a,**kw)
    os._exit(74)
file_exchange.rename=crash
revocation.run(config,Binding(*json.loads(raw)),batch,'apply')
"""
    proc = subprocess.run(
        [sys.executable, "-c", script, str(config.project_root), published[0], json.dumps(values)],
        env={**os.environ, "PYTHONPATH": str(Path(revocation.__file__).parents[1])},
        capture_output=True,
        timeout=20,
    )
    assert proc.returncode == 74, proc.stderr
    assert (
        revocation.run(config, binding, published[0], "status")["results"][0]["state"] == "claimed"
    )
    with pytest.raises(StorageError):
        revocation.run(config, binding, published[0], "apply")
    with patch.object(file_exchange, "rename", side_effect=AssertionError("must not replay")):
        result = revocation.run(config, binding, published[0], "reconcile")
    assert result["results"][0]["state"] == "revoked"
