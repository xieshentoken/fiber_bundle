"""Display computation cannot block navigation or leak after revocation/source changes."""

import asyncio
import json
import sqlite3
import subprocess
import time
from urllib.parse import urlencode

import pytest

from fiber_bundle import auth, cloud_files, database, display_observation, note_index
from fiber_bundle.config import IntegrityError
from fiber_bundle.display_http import PATHS
from fiber_bundle.http_guard import Guard, Settings
from fiber_bundle.vault_scope import Scope


def login(config):
    auth.set_password(config, "synthetic-display-password")
    with database.connect(config.state_root) as db:
        return auth.login(db, "synthetic-display-password", 3600)[0]


def request(token, path="/admin/organization", query=None):
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": urlencode(query or {}).encode(),
        "headers": [(b"host", b"127.0.0.1:8765"), (b"cookie", ("fiber_session=" + token).encode())],
        "client": ("127.0.0.1", 1),
    }


async def call(guard, scope):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await guard(scope, receive, lambda message: append(messages, message))
    return messages


async def append(messages, message):
    messages.append(message)


@pytest.mark.parametrize("endpoint", sorted(PATHS))
@pytest.mark.parametrize("change", ["none", "logout", "metadata", "source", "policy"])
def test_long_display_releases_lease_and_rechecks_before_delivery(config, store, change, endpoint):
    token = login(config)
    path = config.project_root / "test vault/temp_vault1/note.md"
    path.write_text("# synthetic")
    with database.connect(config.state_root) as db:
        vault = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    entered, release = asyncio.Event(), asyncio.Event()

    async def app(scope, receive, send):
        if scope["path"] == endpoint:
            cloud_files.require_available(path)
            entered.set()
            await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"private-result"})

    async def run():
        guard = Guard(app, config, Settings())
        slow = asyncio.create_task(call(guard, request(token, endpoint, query={"vault_id": vault})))
        await asyncio.wait_for(entered.wait(), 3)
        started = time.monotonic()
        response = await call(guard, request(token, "/admin/vaults"))
        assert response[0]["status"] == 200
        assert time.monotonic() - started < 0.5
        with database.locked(config), database.connect(config.state_root) as db:
            if change == "logout":
                db.execute("DELETE FROM admin_sessions")
            elif change == "metadata":
                db.execute("UPDATE vaults SET attachment_state='detached' WHERE id=?", (vault,))
            elif change == "source":
                path.write_text("# replaced")
            elif change == "policy":
                (path.parent / ".knowledge-organizer-policy.json").write_text("{}")
        release.set()
        response = await asyncio.wait_for(slow, 3)
        assert response[0]["status"] == (
            200 if change == "none" else 401 if change == "logout" else 503
        )
        if change not in {"none", "logout"}:
            assert json.loads(response[1]["body"])["error"] == "display_changed"
        if change != "none":
            assert b"private-result" not in b"".join(m.get("body", b"") for m in response)

    asyncio.run(run())


def test_unauthenticated_display_never_computes(config, store):
    called = []

    async def app(*args):
        called.append(True)

    result = asyncio.run(call(Guard(app, config, Settings()), request("invalid")))
    assert result[0]["status"] == 401
    assert not called


def test_batch_checks_are_request_local_and_writes_still_check_each_file(tmp_path, monkeypatch):
    root = tmp_path / "Mobile Documents/vault"
    root.mkdir(parents=True)
    for i in range(120):
        (root / f"{i}.md").write_text("# synthetic")
    (root / "excluded").mkdir()
    (root / "excluded/private.md").write_text("# excluded")
    calls = []
    monkeypatch.setattr(cloud_files, "_helper", lambda: tmp_path / "helper")

    def run(args, **kwargs):
        names = json.loads(kwargs["input"]) if args[1] == "--items" else [args[2]]
        assert all("excluded" not in name for name in names)
        calls.append(args[1])
        return subprocess.CompletedProcess(
            args, 0, b'{"schema":"cloud-status/v1","available":true}'
        )

    monkeypatch.setattr(cloud_files.subprocess, "run", run)

    def read():
        return note_index.snapshot(root, set(), set(), scope=Scope(exclude=("excluded",)))

    for _ in range(2):
        calls.clear()
        observations = {}
        token = display_observation.current.set(observations)
        try:
            assert len(read()) == 120
            display_observation.verify(observations)
        finally:
            display_observation.current.reset(token)
        assert calls == ["--items"]
    calls.clear()
    assert len(read()) == 120
    assert calls == ["--file"] * 120


def test_changed_batched_file_is_rejected(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("before")
    token = display_observation.current.set({})
    try:
        cloud_files.require_available(path)
        path.write_text("after")
        with pytest.raises(IntegrityError, match="display_source_changed"):
            cloud_files.require_available(path)
    finally:
        display_observation.current.reset(token)


@pytest.mark.parametrize("endpoint", sorted(PATHS))
def test_display_tolerates_unrelated_channel_polling(config, store, endpoint):
    token = login(config)
    channel_path = config.state_root / "message-channels.sqlite3"
    with sqlite3.connect(channel_path) as db:
        db.execute("CREATE TABLE polling (value INTEGER)")
        db.execute("INSERT INTO polling VALUES (0)")

    async def app(scope, receive, send):
        # A genuine concurrent transport ledger mutation, not a mocked stamp.
        with sqlite3.connect(channel_path) as db:
            db.execute("UPDATE polling SET value=value+1")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"index-ready"})

    result = asyncio.run(call(Guard(app, config, Settings()), request(token, endpoint)))
    assert result[0]["status"] == 200
    assert b"index-ready" in b"".join(m.get("body", b"") for m in result)


@pytest.mark.parametrize("endpoint", sorted(PATHS))
@pytest.mark.parametrize(
    "dependency",
    [
        "moc-associations.sqlite3",
        "note-search/live.sqlite3",
        "note-search/staging.building-journal",
    ],
)
def test_live_moc_and_index_changes_reject_results_but_build_staging_does_not(
    config, store, endpoint, dependency
):
    token = login(config)
    path = config.state_root / dependency
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"before")

    async def app(scope, receive, send):
        path.write_bytes(b"after")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"moc-result"})

    result = asyncio.run(call(Guard(app, config, Settings()), request(token, endpoint)))
    if dependency.endswith(".building-journal"):
        assert result[0]["status"] == 200
    else:
        assert result[0]["status"] == 503
        assert json.loads(result[1]["body"])["error"] == "display_changed"
        assert b"moc-result" not in b"".join(m.get("body", b"") for m in result)


@pytest.mark.parametrize('change', ['progress', 'scan', 'permission_generation', 'decision'])
def test_catalog_ignores_progress_churn_but_rejects_business_changes(config, store, change):
    from fiber_bundle.organization import store as ledger
    token = login(config)
    job = {'id':'synthetic-job','generation':1,'plan_sha256':'plan','confirmed':True,'completed':0}
    with database.locked(config):
        ledger.put(config,'jobs',job['id'],'synthetic',job)
    async def app(scope, receive, send):
        with database.locked(config):
            if change == 'progress':
                ledger.put(config,'jobs',job['id'],'synthetic',{**job,'completed':1,'activity':{'phase':'unit'}})
            elif change == 'scan':
                ledger.put(config,'package_scans','scan','synthetic',{'checked_at':time.time()})
            elif change == 'permission_generation':
                ledger.put(config,'jobs',job['id'],'synthetic',{**job,'generation':2})
            else:
                ledger.put(config,'decisions','decision','synthetic',{'state':'pinned'})
        await send({'type':'http.response.start','status':200,'headers':[]})
        await send({'type':'http.response.body','body':b'catalog'})
    result = asyncio.run(call(Guard(app,config,Settings()),request(token)))
    assert result[0]['status'] == (200 if change in {'progress','scan'} else 503)
