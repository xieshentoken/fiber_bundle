import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_moves import published as published
from test_weixin_inbox import binding as binding

from fiber_bundle import (
    auth,
    file_exchange,
    moc_associations,
    moc_dispatch,
    moc_dispatch_store,
    moc_file_journal,
    moc_files,
    note_identities,
    note_identity_store,
)
from fiber_bundle.config import StorageError


@pytest.fixture
def dispatch_scope(config, article_scope, published):
    db, result, note, rid = published
    cid, vid, _ = article_scope
    token = "synthetic-moc-dispatch-token"
    db.execute("UPDATE clients SET token_digest=? WHERE id=?", (auth.digest(token), cid))
    root = config.project_root / "test vault/temp_vault1"
    return {
        "authorization": "Bearer " + token,
        "token": token,
        "cid": cid,
        "vid": vid,
        "rid": rid,
        "pid": result["publication_id"],
        "root": root,
        "note": note,
        "db": db,
    }


def moc(config, scope, *, owner="system", track=False):
    mid = str(uuid4())
    path = f"00-Maps/{mid}.md"
    target = scope["root"] / path
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"---\r\ntype: moc\r\n---\r\n# Human MOC\r\n[[manual]]\r\n")
    moc_associations.run(
        config,
        scope["authorization"],
        scope["vid"],
        "create",
        mid,
        title="Synthetic MOC",
        revision=0,
    )
    moc_associations.run(
        config,
        scope["authorization"],
        scope["vid"],
        "link",
        mid,
        revision=1,
        note_identity="publication:" + scope["pid"],
        owner=owner,
        expected_sha256=hashlib.sha256(scope["note"].read_bytes()).hexdigest(),
    )
    if track:
        note_identities.run(
            config,
            scope["authorization"],
            scope["vid"],
            "track",
            path,
            hashlib.sha256(target.read_bytes()).hexdigest(),
        )
    return mid, path


def schedule(config, scope, target, **extra):
    mid, path = target
    return moc_dispatch.schedule(
        config,
        scope["authorization"],
        scope["vid"],
        scope["rid"],
        mid,
        revision=2,
        relative=path,
        expected_sha256=hashlib.sha256((scope["root"] / path).read_bytes()).hexdigest(),
        **extra,
    )


def dispatch(config, scope, **extra):
    return moc_dispatch.dispatch(
        config, scope["authorization"], scope["vid"], enabled=True, maximum=1, **extra
    )


def state(config, scope, job):
    return moc_dispatch.status(config, scope["authorization"], scope["vid"], job["job_id"])


def reconcile(config, scope, job):
    return moc_dispatch.reconcile(config, scope["authorization"], scope["vid"], job["job_id"])


def audit(config, job):
    with moc_dispatch_store.connection(config) as db:
        return [
            row[0]
            for row in db.execute(
                "SELECT state FROM events WHERE job_id=? ORDER BY id", (job["job_id"],)
            )
        ]


def test_disabled_without_reading_or_creating_state(config):
    for kwargs in ({}, {"enabled": True}, {"maximum": 1}):
        assert moc_dispatch.dispatch(config, "invalid", "invalid", **kwargs)["state"] == "disabled"
    assert not config.state_root.exists()


@pytest.mark.parametrize("value", [True, -1, 101, "1", 1.5, None])
def test_invalid_limit_rejected(config, value):
    with pytest.raises(StorageError):
        moc_dispatch.dispatch(config, "invalid", "invalid", enabled=True, maximum=value)


@pytest.mark.parametrize("tracked", [False, True])
@pytest.mark.parametrize("owner", ["manual", "system"])
def test_freeze_then_dispatch_preserves_originals_and_identity(
    config, dispatch_scope, tracked, owner
):
    scope = dispatch_scope
    target = moc(config, scope, track=tracked, owner=owner)
    path = scope["root"] / target[1]
    original, source = path.read_bytes(), scope["note"].read_bytes()
    job = schedule(config, scope, target)
    assert job["state"] == "queued" and path.read_bytes() == original
    assert job == schedule(config, scope, target)
    assert audit(config, job) == ["queued"]
    plan = moc_file_journal.plans(config, scope["vid"], target[0])[0]
    assert plan.value["schema"] == 3 and plan.value["dispatch_source"]["run_id"] == scope["rid"]
    records = note_identity_store.records(config, scope["vid"])
    result = dispatch(config, scope)
    assert result["processed"] == 1 and result["state"] == "limit_reached"
    assert result["results"][0]["moc_file_write_performed"] is True
    assert path.read_bytes().startswith(original)
    from fiber_bundle.organization.note_moc_fields import field

    text = scope["note"].read_text()
    start, end, values, _, _ = field(text)
    assert (text[:start] + text[end:]).encode() == source
    assert values == ["[[" + target[1].removesuffix(".md") + "]]"]
    assert "../article-" in path.read_text()
    assert audit(config, job) == ["queued", "claimed", "applied"]
    assert dispatch(config, scope)["processed"] == 0
    if tracked:
        new = note_identity_store.records(config, scope["vid"])
        assert new[0].identity == records[0].identity and new[0].revision == 2
    with moc_dispatch_store.connection(config) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert [r[0] for r in db.execute("PRAGMA integrity_check")] == ["ok"]
    raw = (config.state_root / "moc-dispatch.sqlite3").read_bytes()
    assert original not in raw and scope["token"].encode() not in raw


def test_total_budget_and_two_threads_do_not_double_apply(config, dispatch_scope):
    scope = dispatch_scope
    first, second = moc(config, scope), moc(config, scope)
    jobs = [schedule(config, scope, first), schedule(config, scope, second)]
    assert dispatch(config, scope)["processed"] == 1
    assert [state(config, scope, j)["state"] for j in jobs] == ["applied", "queued"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: dispatch(config, scope), range(2)))
    assert sum(r["processed"] for r in results) == 1
    assert all(state(config, scope, j)["state"] == "applied" for j in jobs)
    assert all(audit(config, j) == ["queued", "claimed", "applied"] for j in jobs)


@pytest.mark.parametrize("change", ["target", "source", "membership", "revision", "candidate"])
def test_conflicts_stop_queue_no_automatic_replan(config, dispatch_scope, change):
    scope = dispatch_scope
    target = moc(config, scope)
    job = schedule(config, scope, target)
    later = schedule(config, scope, moc(config, scope))
    path = scope["root"] / target[1]
    if change == "target":
        path.write_text("Human new target")
    elif change == "source":
        scope["note"].write_text("Human source edit")
    elif change in {"membership", "revision"}:
        if change == "membership":
            moc_associations.run(
                config,
                scope["authorization"],
                scope["vid"],
                "unlink-system",
                target[0],
                revision=2,
                note_identity="publication:" + scope["pid"],
            )
        else:
            moc_associations.run(
                config,
                scope["authorization"],
                scope["vid"],
                "link",
                target[0],
                revision=2,
                note_identity="publication:" + scope["pid"],
                owner="manual",
                expected_sha256=hashlib.sha256(scope["note"].read_bytes()).hexdigest(),
            )
    else:
        plan = moc_file_journal.plans(config, scope["vid"], target[0])[0]
        replacement = plan.path / "replacement.md"
        replacement.write_bytes((plan.path / "candidate.md").read_bytes())
        replacement.replace(plan.path / "candidate.md")
    before = path.read_bytes(), path.stat().st_ino
    result = dispatch(config, scope)
    assert result["state"] == "stopped" and result["processed"] == 1
    assert state(config, scope, job)["state"] == "failed"
    assert state(config, scope, later)["state"] == "queued"
    assert (path.read_bytes(), path.stat().st_ino) == before
    assert dispatch(config, scope)["state"] == "blocked"
    assert reconcile(config, scope, job)["state"] == "not-applied"
    assert audit(config, job) == ["queued", "claimed", "failed", "not-applied"]


@pytest.mark.parametrize(
    "point", ["job-claim", "file-claim", "after-exchange", "after-file-outcome"]
)
def test_real_process_death_requires_reconcile_without_replay(config, dispatch_scope, point):
    scope = dispatch_scope
    target = moc(config, scope, track=True)
    job = schedule(config, scope, target)
    path = scope["root"] / target[1]
    original = path.read_bytes()
    token = config.project_root / "crash-token.txt"
    token.write_text(scope["token"])
    script = """
import os,sys
from pathlib import Path
from fiber_bundle import moc_dispatch,moc_dispatch_store,moc_files,file_exchange
from fiber_bundle.config import load_config
root=Path(sys.argv[1]);config=load_config(root/'config.json',root);point=sys.argv[3]
transition=moc_dispatch_store.transition;apply=moc_files.apply_file;rename=file_exchange.rename
def job_crash(*a,**k):
    result=transition(*a,**k)
    if a[2]=='claimed': os._exit(92)
    return result
def file_crash(*a,**k):
    if point=='file-claim': os._exit(92)
    rename(*a,**k)
    os._exit(92)
def outcome_crash(*a,**k):
    apply(*a,**k)
    os._exit(92)
if point=='job-claim': moc_dispatch_store.transition=job_crash
elif point=='after-file-outcome': moc_files.apply_file=outcome_crash
else: file_exchange.rename=file_crash
moc_dispatch.dispatch(config,'Bearer '+(root/'crash-token.txt').read_text(),sys.argv[2],
                      enabled=True,maximum=1)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(config.project_root), scope["vid"], point],
        timeout=20,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(moc_dispatch.__file__).parents[1])},
    )
    assert result.returncode == 92
    assert state(config, scope, job)["state"] == "claimed"
    assert dispatch(config, scope)["state"] == "blocked"
    before = path.read_bytes(), path.stat().st_ino
    result = reconcile(config, scope, job)
    applied = point in {"after-exchange", "after-file-outcome"}
    assert result["state"] == ("applied" if applied else "not-applied")
    assert result["moc_file_write_performed"] is False
    assert (path.read_bytes(), path.stat().st_ino) == before
    assert (path.read_bytes() != original) is applied
    records = note_identity_store.records(config, scope["vid"])
    assert records[0].revision == (2 if applied else 1)


@pytest.mark.parametrize("change", ["run-cancelled", "add", "query", "client"])
def test_revocation_or_cancel_before_dispatch_does_not_write(config, dispatch_scope, change):
    scope = dispatch_scope
    target = moc(config, scope)
    job = schedule(config, scope, target)
    if change == "run-cancelled":
        scope["db"].execute(
            "UPDATE execution_runs SET state='cancelled' WHERE id=?", (scope["rid"],)
        )
    elif change == "client":
        scope["db"].execute("UPDATE clients SET active=0 WHERE id=?", (scope["cid"],))
    else:
        auth.set_grant(
            scope["db"],
            scope["cid"],
            scope["vid"],
            query=change != "query",
            add=change != "add",
            original=False,
        )
    path = scope["root"] / target[1]
    before = path.read_bytes()
    if change == "run-cancelled":
        assert dispatch(config, scope)["state"] == "stopped"
        assert reconcile(config, scope, job)["state"] == "not-applied"
    else:
        with pytest.raises(auth.Denied):
            dispatch(config, scope)
    assert path.read_bytes() == before


def test_durable_file_plan_cannot_bypass_cancel_via_legacy_cli_api(config, dispatch_scope):
    scope = dispatch_scope
    target = moc(config, scope, track=True)
    job = schedule(config, scope, target)
    scope["db"].execute("UPDATE execution_runs SET state='cancelled' WHERE id=?", (scope["rid"],))
    with pytest.raises(StorageError):
        moc_files.run(
            config,
            scope["authorization"],
            scope["vid"],
            "apply-file",
            target[0],
            plan_id=job["plan_id"],
        )
    assert state(config, scope, job)["state"] == "queued"
    assert moc_file_journal.plans(config, scope["vid"], target[0])[0].status == "planned"


def test_source_cancel_at_exchange_rolls_back_before_identity_commit(config, dispatch_scope):
    scope = dispatch_scope
    target = moc(config, scope, track=True)
    job = schedule(config, scope, target)
    path = scope["root"] / target[1]
    original, inode = path.read_bytes(), path.stat().st_ino
    rename = file_exchange.rename
    calls = 0

    def cancel(*args, **kwargs):
        nonlocal calls
        result = rename(*args, **kwargs)
        calls += 1
        if calls == 1:
            scope["db"].execute(
                "UPDATE execution_runs SET state='cancelled' WHERE id=?", (scope["rid"],)
            )
        return result

    with patch.object(file_exchange, "rename", side_effect=cancel):
        assert dispatch(config, scope)["state"] == "stopped"
    assert (path.read_bytes(), path.stat().st_ino) == (original, inode)
    assert note_identity_store.records(config, scope["vid"])[0].revision == 1
    assert reconcile(config, scope, job)["state"] == "not-applied"


def test_unpublished_or_unclassified_missing_or_other_owner_is_not_scheduled(
    config, dispatch_scope
):
    scope = dispatch_scope
    target = moc(config, scope)
    original = scope["note"].read_bytes()
    scope["note"].unlink()
    with pytest.raises(StorageError):
        schedule(config, scope, target)
    scope["note"].write_bytes(original)
    moc_associations.run(
        config,
        scope["authorization"],
        scope["vid"],
        "unlink-system",
        target[0],
        revision=2,
        note_identity="publication:" + scope["pid"],
    )
    with pytest.raises(StorageError):
        schedule(config, scope, target)
    assert not (config.state_root / "moc-dispatch.sqlite3").exists()


def test_stop_event_prevents_next_claim(config, dispatch_scope):
    scope = dispatch_scope
    job = schedule(config, scope, moc(config, scope))
    stop = Event()
    stop.set()
    assert dispatch(config, scope, stop=stop)["state"] == "interrupted"
    assert audit(config, job) == ["queued"]


def test_read_only_status_is_scoped_and_no_ledger_creation(config, dispatch_scope):
    scope = dispatch_scope
    other = auth.create_client(scope["db"], "other-owner", False)
    auth.set_grant(scope["db"], other["id"], scope["vid"], query=True, add=True, original=False)
    with pytest.raises(auth.Denied):
        moc_dispatch.status(config, "Bearer " + other["token"], scope["vid"], "0" * 64)
    assert not (config.state_root / "moc-dispatch.sqlite3").exists()
    job = schedule(config, scope, moc(config, scope))
    with pytest.raises(auth.Denied):
        moc_dispatch.status(config, "Bearer " + other["token"], scope["vid"], job["job_id"])
    with pytest.raises(auth.Denied):
        moc_dispatch.reconcile(config, "Bearer " + other["token"], scope["vid"], job["job_id"])
    auth.set_grant(scope["db"], scope["cid"], scope["vid"], query=True, add=False, original=False)
    assert state(config, scope, job)["state"] == "queued"


def test_actual_cli_default_off_plan_dispatch_and_safe_errors(config, dispatch_scope):
    scope = dispatch_scope
    target = moc(config, scope)
    token = config.project_root / "cli-token.txt"
    token.write_text(scope["token"])
    common = [
        sys.executable,
        "-m",
        "fiber_bundle.moc_dispatch_cli",
        "--project-root",
        str(config.project_root),
        "--config",
        "config.json",
        "--token-file",
        str(token),
        "--vault-id",
        scope["vid"],
    ]

    def cli(*args):
        return subprocess.run(
            common + list(args),
            capture_output=True,
            text=True,
            timeout=20,
            env={**os.environ, "PYTHONPATH": str(Path(moc_dispatch.__file__).parents[1])},
        )

    assert json.loads(cli("dispatch").stdout)["state"] == "disabled"
    result = cli(
        "schedule",
        "--run-id",
        scope["rid"],
        "--moc-id",
        target[0],
        "--revision",
        "2",
        "--moc-path",
        target[1],
        "--expected-sha256",
        hashlib.sha256((scope["root"] / target[1]).read_bytes()).hexdigest(),
    )
    assert result.returncode == 0
    job = json.loads(result.stdout)
    assert cli("status", "--job-id", job["job_id"]).returncode == 0
    assert cli("dispatch", "--enabled", "--maximum", "1").returncode == 0
    bad = cli("dispatch", "--enabled", "--maximum", "101")
    assert (
        bad.returncode == 1
        and scope["token"] not in bad.stdout
        and str(scope["root"]) not in bad.stdout
    )
