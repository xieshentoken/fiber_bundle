"""Display versions follow the selected vault and the response's own dependencies."""

import asyncio
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from starlette.requests import Request
from test_display_reads import call, login, request, selected_vault

from fiber_bundle import auth, database, display_dependencies
from fiber_bundle.config import InvalidInput
from fiber_bundle.display_http import PATHS
from fiber_bundle.http_guard import Guard, Settings
from fiber_bundle.organization import store as ledger
from fiber_bundle.vault_scope import Scope
from fiber_bundle.vault_sync import store as observer


def test_display_stamp_can_read_current_schema(config, store):
    display_dependencies.stamp(config, Request(request(login(config))))


@pytest.mark.parametrize("vault", ["../private", "/private/tmp", "bad/id", "", None])
def test_invalid_or_absent_vault_cannot_traverse_journals(config, store, monkeypatch, vault):
    from fiber_bundle import note_search_cache

    token = login(config)
    monkeypatch.setattr(Path, "rglob", lambda *_: pytest.fail("unvalidated directory traversal"))
    monkeypatch.setattr(
        note_search_cache, "location", lambda *_: pytest.fail("unvalidated index path")
    )
    query = {} if vault is None else {"vault_id": vault}
    if vault:
        with pytest.raises(InvalidInput):
            display_dependencies.stamp(config, Request(request(token, query=query)))
    else:
        display_dependencies.stamp(config, Request(request(token, query=query)))


def test_missing_canonical_vault_does_not_probe_partitioned_paths(config, store, monkeypatch):
    from fiber_bundle import note_search_cache

    token = login(config)
    monkeypatch.setattr(Path, "rglob", lambda *_: pytest.fail("missing vault traversal"))
    monkeypatch.setattr(
        note_search_cache, "location", lambda *_: pytest.fail("missing vault index")
    )
    display_dependencies.stamp(config, Request(request(token, query={"vault_id": str(uuid4())})))


@pytest.mark.parametrize("endpoint", sorted(PATHS))
@pytest.mark.parametrize("change", ["observer", "weixin", "publication", "decision", "live_index"])
def test_other_vault_and_transport_changes_never_discard_current_display(
    config, store, endpoint, change
):
    token = login(config)
    current = selected_vault(config)
    with database.connect(config.state_root) as db:
        other = db.execute("SELECT id FROM vaults WHERE id<>?", (current,)).fetchone()[0]
    observer.register(
        config, other, str(config.project_root / "test vault/temp_vault2"), Scope(), "test"
    )
    transport = config.state_root / "weixin-inbox.sqlite3"
    with sqlite3.connect(transport) as db:
        db.execute("CREATE TABLE polling(cursor INTEGER)")
        db.execute("INSERT INTO polling VALUES(0)")

    async def app(scope, receive, send):
        if change == "observer":
            observer.enqueue(config, other, [("unrelated.md", 2)], "17")
        elif change == "weixin":
            with sqlite3.connect(transport) as db:
                db.execute("UPDATE polling SET cursor=cursor+1")
        elif change == "publication":
            with database.connect(config.state_root) as db:
                db.execute(
                    "INSERT INTO publication_locations "
                    "VALUES('intake',?,?,?,NULL,1,'published','{}')",
                    ("other-publication", other, "other.md"),
                )
        elif change == "decision":
            ledger.put(config, "decisions", "other-decision", other, {"state": "rejected"})
        else:
            from fiber_bundle.note_search_cache import location

            path = location(config, other)
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"new other vault index")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"current-vault"})

    messages = asyncio.run(
        call(Guard(app, config, Settings()), request(token, endpoint, {"vault_id": current}))
    )
    assert messages[0]["status"] == 200
    assert messages[-1]["body"] == b"current-vault"


@pytest.mark.parametrize(
    "change", ["grant", "negative_decision", "publication", "root", "operation"]
)
def test_current_authority_and_business_dependencies_discard_buffer(config, store, change):
    token = login(config)
    vault = selected_vault(config)
    with database.connect(config.state_root) as db:
        client = auth.create_client(db, "display client", False)["id"]
        auth.set_grant(db, client, vault, query=True, original=False, add=False)

    async def app(scope, receive, send):
        if change == "negative_decision":
            ledger.put(config, "decisions", "decision", vault, {"state": "rejected"})
        else:
            with database.connect(config.state_root) as db:
                if change == "grant":
                    auth.set_grant(db, client, vault, query=False, original=False, add=False)
                elif change == "root":
                    db.execute("UPDATE vaults SET relative_path='changed' WHERE id=?", (vault,))
                elif change == "operation":
                    db.execute(
                        "INSERT INTO vault_operations "
                        "VALUES(?,?,'note_placement','prepared','{}',1,1)",
                        (str(uuid4()), vault),
                    )
                else:
                    db.execute(
                        "INSERT INTO publication_locations "
                        "VALUES('intake',?,?,?,NULL,1,'published','{}')",
                        ("current-publication", vault, "new.md"),
                    )
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"old-private-buffer"})

    result = asyncio.run(
        call(
            Guard(app, config, Settings()),
            request(token, query={"vault_id": vault, "client_id": client}),
        )
    )
    assert result[0]["status"] == 503
    assert b"old-private-buffer" not in b"".join(m.get("body", b"") for m in result)


def test_stamp_does_not_materialize_large_archival_payloads(config, store):
    token = login(config)
    vault = selected_vault(config)
    with database.connect(config.state_root) as db:
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,'note_placement','prepared',?,1,1)",
            (str(uuid4()), vault, json.dumps({"large_snapshot": "x" * 2_000_000})),
        )
    value = display_dependencies.stamp(config, Request(request(token, query={"vault_id": vault})))
    assert len(repr(value)) < 10000
    assert "large_snapshot" not in repr(value)


def test_same_vault_attachment_observation_does_not_invalidate_catalog(config, store):
    token = login(config)
    vault = selected_vault(config)
    observer.register(
        config, vault, str(config.project_root / "test vault/temp_vault1"), Scope(), "test"
    )

    async def app(scope, receive, send):
        observer.enqueue(config, vault, [("image.png", 2)], "17")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"catalog"})

    result = asyncio.run(
        call(Guard(app, config, Settings()), request(token, query={"vault_id": vault}))
    )
    assert result[0]["status"] == 200


@pytest.mark.parametrize("change", ["revoke", "revoke_restore", "unrelated"])
def test_actual_source_authorization_is_rechecked_including_revocation_history(
    config, store, change
):
    from fiber_bundle import display_observation

    token = login(config)
    current = selected_vault(config)
    with database.connect(config.state_root) as db:
        source = db.execute("SELECT id FROM vaults WHERE id<>?", (current,)).fetchone()[0]
        reader = auth.create_client(db, "analysis reader", False)["id"]
        author = auth.create_client(db, "analysis author", False)["id"]
        unrelated = auth.create_client(db, "unrelated", False)["id"]
        for person, vault in ((reader, current), (author, source), (unrelated, source)):
            auth.set_grant(db, person, vault, query=True, original=False, add=False)

    async def app(scope, receive, send):
        with database.connect(config.state_root) as db:
            # This mirrors saved-analysis provenance, whose author/source pair differs
            # from the requested reader/current vault and is discovered during reading.
            auth.grant(db, author, source, "query")
            who = unrelated if change == "unrelated" else author
            auth.set_grant(db, who, source, query=False, original=False, add=False)
            if change == "revoke_restore":
                auth.set_grant(db, who, source, query=True, original=False, add=False)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"source-derived-private-result"})

    result = asyncio.run(
        call(
            Guard(app, config, Settings()),
            request(
                token, "/admin/knowledge-graph/notes", {"vault_id": current, "client_id": reader}
            ),
        )
    )
    assert result[0]["status"] == (
        200 if change == "unrelated" else 404 if change == "revoke" else 503
    )
    if change != "unrelated":
        assert b"source-derived-private-result" not in b"".join(m.get("body", b"") for m in result)
    assert display_observation.authorizations.get() is None


@pytest.mark.parametrize("change", ["heartbeat", "expired"])
def test_status_binds_observer_availability_but_not_fresh_heartbeat_churn(
    config, store, monkeypatch, change
):
    import time

    token = login(config)
    vault = selected_vault(config)
    now = [time.time()]
    observer.register(
        config, vault, str(config.project_root / "test vault/temp_vault1"), Scope(), "test"
    )
    with observer.connection(config, write=True) as db:
        db.execute("UPDATE libraries SET heartbeat=? WHERE id=?", (now[0] - 44, vault))
    monkeypatch.setattr(display_dependencies.time, "time", lambda: now[0])

    async def app(scope, receive, send):
        if change == "heartbeat":
            with observer.connection(config, write=True) as db:
                db.execute("UPDATE libraries SET heartbeat=? WHERE id=?", (now[0], vault))
        else:
            now[0] += 2
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"fresh-index-status"})

    result = asyncio.run(
        call(
            Guard(app, config, Settings()),
            request(token, "/admin/note-index/status", {"vault_id": vault}),
        )
    )
    assert result[0]["status"] == (200 if change == "heartbeat" else 503)


def test_new_package_failure_is_a_status_dependency_without_scan_progress_churn(config, store):
    import time

    from fiber_bundle import note_search_jobs
    from fiber_bundle.vault_sync import explicit_refresh

    token = login(config)
    vault = selected_vault(config)
    client = str(uuid4())
    job = note_search_jobs.record(config, vault, "pending", reason="explicit_update")
    explicit_refresh._write(
        config,
        {
            "schema": "vault-index-refresh/v1",
            "vault_id": vault,
            "client_id": client,
            "job_id": job,
            "requested_at": time.time(),
            "phase": "queued",
            "packages": True,
        },
    )
    key = vault + ":" + client
    scan = {"state": "ready", "request_id": job, "items": []}
    ledger.put(config, "package_scans", key, vault, scan)

    async def app(scope, receive, send):
        ledger.put(
            config,
            "package_scans",
            key,
            vault,
            {**scan, "items": [{"path": "Book/broken", "state": "failed"}]},
        )
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ready-without-failed-package"})

    result = asyncio.run(
        call(
            Guard(app, config, Settings()),
            request(token, "/admin/note-index/status", {"vault_id": vault}),
        )
    )
    assert result[0]["status"] == 503
    assert b"ready-without-failed-package" not in b"".join(m.get("body", b"") for m in result)
