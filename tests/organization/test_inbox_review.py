"""Synthetic current-file review, atomic recovery and unchanged publication evidence."""

import copy
import json
import os
from uuid import uuid4

import pytest
from test_jobs import ready as ready
from test_maintenance import preview as maintenance_preview
from test_note_moc_locations import managed
from test_publication_layout import activate

from fiber_bundle import (
    auth,
    database,
    file_exchange,
    note_identity_store,
    note_inventory,
    publication_file_io,
    publication_links,
    publication_locations,
)
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.organization import inbox_review as review
from fiber_bundle.organization import inbox_review_execution as execution
from fiber_bundle.organization import inbox_review_plan as plans
from fiber_bundle.organization import inbox_review_publication, maintenance, store


@pytest.fixture
def inbox(config, scope):
    client, vault, root = scope
    activate(config, vault)
    path = "Inbox/review.md"
    note = root / path
    note.write_bytes(b"")
    identity = str(uuid4())
    job = {
        "id": identity,
        "vault_id": vault,
        "client_id": client,
        "state": "preview",
        "items": [{"path": path, "archive_preflight": {"links": []}}],
    }
    store.put(config, "jobs", identity, vault, job)
    return client, vault, root, path, identity, job


def report(config, inbox, targets):
    *_, path, identity, job = inbox
    job["items"][0]["archive_preflight"]["links"] = [
        {"note_path": path, "line": line, "target": target} for line, target in targets
    ]
    store.put(config, "jobs", identity, inbox[1], job)


def plan(config, db, inbox, action="quarantine_empty", link_ids=None):
    client, vault, _, path, identity, _ = inbox
    return plans.preview(config, db, client, vault, identity, path, action, link_ids or [])


def apply(config, db, inbox, operation):
    return execution.apply(
        config, db, *inbox[:2], operation["operation_id"], operation["plan_sha256"]
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b"", "empty"),
        (b" \r\n\t", "empty"),
        (b"\xef\xbb\xbf# Plain title\r\n", "title_only"),
        (b"# [[Title]]\n", "content"),
        (b"# ![image](a.png)\n", "content"),
        (b"# `code`\n", "content"),
        (b"# <span>title</span>\n", "content"),
        (b"# One\n# Two\n", "content"),
        (b"# Heading\nbody\n", "content"),
    ],
)
def test_current_classification_and_quarantine_gate(config, inbox, raw, expected):
    client, vault, root, path, identity, _ = inbox
    (root / path).write_bytes(raw)
    with database.connect(config.state_root) as db:
        current = review.inspect(config, db, client, vault, identity, path)
        assert current["classification"] == expected
        assert current["byte_count"] == len(raw)
        if expected == "content":
            with pytest.raises(InvalidInput, match="not_empty"):
                plan(config, db, inbox)
    assert (root / path).read_bytes() == raw


@pytest.mark.parametrize(
    "raw",
    [b"\xff", b"\x00bad", b"x" * (1024 * 1024 + 1)],
    ids=["invalid-utf8", "nul-content", "oversized"],
)
def test_unreadable_is_never_empty(config, inbox, raw):
    client, vault, root, path, identity, _ = inbox
    (root / path).write_bytes(raw)
    with database.connect(config.state_root) as db:
        current = review.inspect(config, db, client, vault, identity, path)
        assert current["classification"] == ("unreadable" if raw[0] != 0 else "content")
        assert not current["actions"]["quarantine_empty"]


def test_quarantine_restore_no_overwrite_and_original_history_preserved(config, inbox):
    client, vault, root, path, _, job = inbox
    note = root / path
    note.write_text("# Empty subject\n")
    original = note.read_bytes()
    attached = root / "Inbox/figure.png"
    attached.write_bytes(b"original attachment")
    with database.connect(config.state_root) as db:
        prepared = plan(config, db, inbox)
        assert note.exists()  # A preview never moves a source.
        result = apply(config, db, inbox, prepared)
        assert result["state"] == "completed" and result["can_restore"]
        assert not note.exists()
        assert apply(config, db, inbox, prepared) == result
        note.write_text("do not overwrite")
        with pytest.raises(IntegrityError, match="path_occupied"):
            plans.restore_preview(config, db, client, vault, prepared["operation_id"])
        note.rename(root / "Inbox/human-moved.md")
        restoring = plans.restore_preview(config, db, client, vault, prepared["operation_id"])
        assert not note.exists()
        assert apply(config, db, inbox, restoring)["state"] == "completed"
        assert note.read_bytes() == original
        assert apply(config, db, inbox, restoring)["state"] == "completed"
        assert (
            plans.restore_preview(config, db, client, vault, prepared["operation_id"])[
                "operation_id"
            ]
            == restoring["operation_id"]
        )
        assert store.get(config, "jobs", job["id"]) == job
        assert attached.read_bytes() == b"original attachment"


def test_move_crash_is_explicitly_reconciled_without_duplicate_move(config, inbox, monkeypatch):
    _, _, root, path, *_ = inbox
    actual = file_exchange.rename
    calls = []

    def move_and_crash(*args, **kwargs):
        actual(*args, **kwargs)
        calls.append(args)
        raise RuntimeError("synthetic crash after rename")

    with database.connect(config.state_root) as db:
        prepared = plan(config, db, inbox)
        monkeypatch.setattr(file_exchange, "rename", move_and_crash)
        with pytest.raises(RuntimeError, match="synthetic crash"):
            apply(config, db, inbox, prepared)
        assert not (root / path).exists()
        assert plans.history(config, db, *inbox[:2])["operations"][0]["can_reconcile"]
        monkeypatch.setattr(file_exchange, "rename", actual)
        assert apply(config, db, inbox, prepared)["state"] == "completed"
        assert len(calls) == 1


def test_concurrent_content_move_has_explicit_gui_restore(config, inbox, monkeypatch):
    client, vault, root, path, *_ = inbox
    actual = file_exchange.rename

    def racing_move(*args, **kwargs):
        (root / path).write_text("external human body\n")
        return actual(*args, **kwargs)

    with database.connect(config.state_root) as db:
        prepared = plan(config, db, inbox)
        monkeypatch.setattr(file_exchange, "rename", racing_move)
        with pytest.raises(IntegrityError, match="concurrent_edit_preserved"):
            apply(config, db, inbox, prepared)
        item = plans.history(config, db, client, vault)["operations"][0]
        assert item["can_restore"] and not item["can_reconcile"]
        monkeypatch.setattr(file_exchange, "rename", actual)
        restoring = plans.restore_preview(config, db, client, vault, prepared["operation_id"])
        assert apply(config, db, inbox, restoring)["state"] == "completed"
        assert (root / path).read_text() == "external human body\n"


@pytest.mark.parametrize("change", ["content", "replace", "mode", "parent", "scope", "revoke"])
def test_stale_confirmation_and_current_authorization(config, inbox, change):
    client, vault, root, path, *_ = inbox
    with database.connect(config.state_root) as db:
        prepared = plan(config, db, inbox)
        if change == "content":
            (root / path).write_text("human text")
        elif change == "replace":
            (root / path).rename(root / "Inbox/keep.md")
            (root / path).write_bytes(b"")
        elif change == "mode":
            (root / path).chmod(0o400)
        elif change == "parent":
            (root / "Inbox").chmod((root / "Inbox").stat().st_mode ^ 0o040)
        elif change == "scope":
            db.execute(
                "UPDATE vaults SET scope_json=? WHERE id=?",
                (json.dumps({"include": ["Inbox"], "exclude": []}), vault),
            )
        else:
            auth.set_grant(db, client, vault, query=False, original=False, add=False)
        with pytest.raises((IntegrityError, InvalidInput, auth.Denied)):
            apply(config, db, inbox, prepared)
        assert (root / path).exists()


def test_links_preserve_every_other_byte_and_restore_after_exchange_crash(
    config, inbox, monkeypatch
):
    client, vault, root, path, identity, _ = inbox
    raw = (
        b"\xef\xbb\xbf# EELS\r\n"
        + "图1: [110](上) 和 [111](下)。[[gone|文字]]\r\n".encode()
        + b"![image](missing) ![[image-missing]] [[valid]] `[[code]]`\r\n"
    )
    (root / path).write_bytes(raw)
    (root / "Inbox/valid.md").write_text("# valid\n")
    report(
        config,
        inbox,
        [
            (2, "上"),
            (2, "下"),
            (2, "gone"),
            (3, "missing"),
            (3, "image-missing"),
            (3, "valid"),
            (3, "code"),
        ],
    )
    with database.connect(config.state_root) as db:
        current = review.inspect(config, db, client, vault, identity, path)
        assert {link["target"] for link in current["links"]} == {"上", "下", "gone"}
        selected = [link["id"] for link in current["links"] if link["target"] != "gone"]
        prepared = plan(config, db, inbox, "unlink_missing", selected)
        assert len(prepared["changes"]) == 1
        assert "\\[110\\](上)" in prepared["changes"][0]["after"]
        actual = file_exchange.rename

        def exchange_and_crash(*args, **kwargs):
            actual(*args, **kwargs)
            raise RuntimeError("synthetic exchange crash")

        monkeypatch.setattr(file_exchange, "rename", exchange_and_crash)
        with pytest.raises(RuntimeError):
            apply(config, db, inbox, prepared)
        after = raw.replace(b"[110]", b"\\[110\\]").replace(b"[111]", b"\\[111\\]")
        assert (root / path).read_bytes() == after
        monkeypatch.setattr(file_exchange, "rename", actual)
        assert apply(config, db, inbox, prepared)["state"] == "completed"
        restoring = plans.restore_preview(config, db, client, vault, prepared["operation_id"])
        apply(config, db, inbox, restoring)
        assert (root / path).read_bytes() == raw


def test_link_stale_valid_or_ambiguous_and_unknown_ids_refused(config, inbox):
    client, vault, root, path, identity, _ = inbox
    (root / path).write_text("[[missing]] [[ambiguous]] [[unreported]]\n")
    for parent in ("One", "Two"):
        (root / parent).mkdir()
        (root / parent / "ambiguous.md").write_text("# content")
    report(config, inbox, [(1, "missing"), (1, "ambiguous")])
    with database.connect(config.state_root) as db:
        current = review.inspect(config, db, client, vault, identity, path)
        assert [row["target"] for row in current["links"]] == ["missing"]
        with pytest.raises(InvalidInput, match="link_selection"):
            plan(config, db, inbox, "unlink_missing", ["unknown"])
        prepared = plan(config, db, inbox, "unlink_missing", [current["links"][0]["id"]])
        (root / "Inbox/missing.md").write_text("# now exists")
        with pytest.raises(IntegrityError, match="link_selection"):
            apply(config, db, inbox, prepared)


def test_pending_publication_cannot_write_back_even_after_restore(config, inbox):
    client, vault, root, path, *_ = inbox
    publication = str(uuid4())
    row = {
        "vault_id": vault,
        "kind": "intake",
        "publication_id": publication,
        "state": "committing",
        "plan": {"mapping": {"note.md": path}, "outputs": {path: ""}},
    }
    before = copy.deepcopy(row)
    with database.connect(config.state_root) as db:
        db.execute(
            "INSERT INTO publication_locations VALUES('intake',?,?,?,NULL,1,'committing',?)",
            (publication, vault, path, canonical(row["plan"])),
        )
        prepared = plan(config, db, inbox)
        inbox_review_publication.guard(config, row)
        apply(config, db, inbox, prepared)
        with pytest.raises(IntegrityError, match="publication_quarantined"):
            inbox_review_publication.guard(config, row)
        restoring = plans.restore_preview(config, db, client, vault, prepared["operation_id"])
        apply(config, db, inbox, restoring)
        assert (root / path).exists()
        with pytest.raises(IntegrityError, match="publication_quarantined"):
            inbox_review_publication.guard(config, row)
        inbox_review_publication.guard(config, {**row, "state": "published"})
        inbox_review_publication.guard(config, {**row, "publication_id": str(uuid4())})
        assert publication_locations.get(db, "intake", publication)["state"] == "committing"
        from fiber_bundle import publication_commit

        with pytest.raises(IntegrityError, match="publication_quarantined"):
            publication_commit.check(config, db, row, client)
        assert row == before


def test_http_admin_csrf_path_and_filtered_history(config, inbox, http):
    client, vault, root, path, identity, _ = inbox
    data = {"client_id": client, "vault_id": vault, "job_id": identity, "path": path}
    prefix = "/admin/organization/inbox-review"
    assert http.post(prefix + "/inspect", json=data).status_code == 200
    assert http.post(prefix + "/inspect", json={**data, "path": "../private.md"}).status_code in {
        400,
        404,
        409,
    }
    assert (
        http.post(prefix + "/inspect", json={**data, "path": "Inbox/unknown.md"}).status_code == 404
    )
    (root / "Inbox/link.md").symlink_to(root / path)
    assert http.post(prefix + "/inspect", json={**data, "path": "Inbox/link.md"}).status_code in {
        400,
        404,
        409,
    }
    csrf = http.headers.pop("X-CSRF-Token")
    assert (
        http.post(prefix + "/preview", json={**data, "action": "quarantine_empty"}).status_code
        == 403
    )
    http.headers["X-CSRF-Token"] = csrf
    response = http.post(prefix + "/preview", json={**data, "action": "quarantine_empty"})
    assert response.status_code == 200, response.text
    operation = response.json()
    assert (
        http.post(
            prefix + f"/operations/{operation['operation_id']}/apply",
            json={"client_id": client, "vault_id": vault, "expected_plan_sha256": "0" * 64},
        ).status_code
        == 409
    )
    with database.connect(config.state_root) as db:
        auth.set_grant(db, client, vault, query=False, original=False, add=False)
    assert (
        http.get(prefix + "/history", params={"client_id": client, "vault_id": vault}).status_code
        == 404
    )


def test_escaped_openings_are_plain_text_but_even_slashes_still_link():
    text = r"\[110\](上) \[[gone]] \\[label](target) [valid](real)"
    refs = publication_links.references(text)
    assert [text[start:stop] for _, _, _, start, stop in refs] == [
        "[label](target)",
        "[valid](real)",
    ]


def test_managed_repair_restore_then_fresh_quick_archive_preserve_baseline(config, ready):
    client, vault, root, _, note = ready
    note.write_bytes(note.read_bytes() + "\n图1: [110](上) 和 [111](下)。\n".encode())
    publication, raw, _ = managed(config, ready)
    path = "Inbox/传热.md"
    original_key = publication_file_io.read(root, path)[1]
    registration = note_identity_store.Registration(
        publication_locations.note_id("intake", publication),
        None,
        path,
        digest(raw),
        tuple(original_key),
        (root.stat().st_dev, root.stat().st_ino),
        1,
    )
    note_identity_store.save(config, vault, client, registration)
    with database.connect(config.state_root) as db:
        location = publication_locations.get(db, "intake", publication)
        location["plan"]["source_spans"] = {"status": "original", "published_units": []}
        db.execute(
            "UPDATE publication_locations SET value=? WHERE kind='intake' AND publication_id=?",
            (canonical(location["plan"]), publication),
        )
        original = copy.deepcopy(location["plan"])
        job = maintenance_preview(config, db, client, vault, "quick")
        assert job["items"][0]["archive_preflight"]["state"] == "blocked"
        current = review.inspect(config, db, client, vault, job["id"], path)
        assert {link["target"] for link in current["links"]} == {"上", "下"}
        prepared = plans.preview(
            config,
            db,
            client,
            vault,
            job["id"],
            path,
            "unlink_missing",
            [link["id"] for link in current["links"]],
        )
        execution.apply(
            config, db, client, vault, prepared["operation_id"], prepared["plan_sha256"]
        )
        updated = publication_locations.get(db, "intake", publication)
        for field in ("outputs", "published_hashes", "source_spans"):
            assert updated["plan"][field] == original[field]
        assert updated["state"] == "published"
        new_key = publication_file_io.read(root, path)[1]
        assert new_key != original_key
        assert updated["plan"]["file_keys"][path] == new_key
        inventory = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
        assert any(n.relative_path == path for n in inventory.notes)
        assert note_identity_store.records(config, vault)[0].file_key == tuple(new_key)
        restoring = plans.restore_preview(config, db, client, vault, prepared["operation_id"])
        execution.apply(
            config, db, client, vault, restoring["operation_id"], restoring["plan_sha256"]
        )
        assert note.read_bytes() == raw
        restored_key = publication_file_io.read(root, path)[1]
        assert restored_key != original_key
        assert note_identity_store.records(config, vault)[0].file_key == tuple(restored_key)
        restored = publication_locations.get(db, "intake", publication)
        for field in ("outputs", "published_hashes", "source_spans"):
            assert restored["plan"][field] == original[field]
        # Reapply from a new inspection, then run the real fresh-preview/confirmation/archive path.
        current = review.inspect(config, db, client, vault, job["id"], path)
        repaired = plans.preview(
            config,
            db,
            client,
            vault,
            job["id"],
            path,
            "unlink_missing",
            [link["id"] for link in current["links"]],
        )
        execution.apply(
            config, db, client, vault, repaired["operation_id"], repaired["plan_sha256"]
        )
        fresh = maintenance_preview(config, db, client, vault, "quick")
        assert fresh["items"][0]["archive_preflight"]["state"] == "ready"
        assert fresh["state"] == "preview" and not fresh["confirmed"]
        maintenance.action(config, db, fresh, "confirm", fresh["plan_sha256"])
        for _ in range(8):
            maintenance.run(config, db, fresh)
            if fresh["state"] not in {"queued", "running"}:
                break
        assert fresh["state"] == "completed", fresh
        assert not note.exists() and (root / "Resource/传热.md").exists()


def test_claimed_multibyte_partial_payload_recovers_without_touching_source(config, inbox):
    client, vault, root, path, identity, _ = inbox
    (root / path).write_text("[[gone|中文]]\n")
    report(config, inbox, [(1, "gone")])
    with database.connect(config.state_root) as db:
        link = review.inspect(config, db, client, vault, identity, path)["links"][0]
        prepared = plan(config, db, inbox, "unlink_missing", [link["id"]])
        value = plans.load(config, db, client, vault, prepared["operation_id"])
        execution.create_stage(config, root, value)
        partial = "中".encode()[:1]
        with execution.stage(root, value) as fd:
            handle = os.open("payload-1.md", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
            try:
                key = os.fstat(handle)
                os.write(handle, partial)
                os.fsync(handle)
            finally:
                os.close(handle)
        value.update(
            payload_name="payload-1.md",
            payload_attempt=1,
            payload_key=[key.st_dev, key.st_ino],
            payload_ready=False,
        )
        plans.save(config, value, "reconcile")
        assert (root / path).read_text() == "[[gone|中文]]\n"
        assert apply(config, db, inbox, prepared)["state"] == "completed"
        assert (root / path).read_text() == "中文\n"


@pytest.mark.parametrize("change", ["edit", "occupied", "backup", "parent", "symlink"])
def test_restore_preview_and_stale_restore_confirmation_are_safe(config, inbox, change):
    client, vault, root, path, identity, _ = inbox
    (root / path).write_text("[[gone]]\n")
    report(config, inbox, [(1, "gone")])
    with database.connect(config.state_root) as db:
        link = review.inspect(config, db, client, vault, identity, path)["links"][0]
        prepared = plan(config, db, inbox, "unlink_missing", [link["id"]])
        apply(config, db, inbox, prepared)
        restoring = plans.restore_preview(config, db, client, vault, prepared["operation_id"])
        value = plans.load(config, db, client, vault, prepared["operation_id"])
        if change == "edit":
            (root / path).write_text("human body")
        elif change == "occupied":
            (root / path).rename(root / "Inbox/keep.md")
            (root / path).write_text("gone\n")
        elif change == "backup":
            (root / value["stage_name"] / value["payload_name"]).write_text("changed backup")
        elif change == "parent":
            (root / "Inbox").chmod((root / "Inbox").stat().st_mode ^ 0o040)
        else:
            (root / path).rename(root / "Inbox/keep.md")
            (root / path).symlink_to(root / "Inbox/keep.md")
        with pytest.raises((IntegrityError, InvalidInput)):
            apply(config, db, inbox, restoring)


def test_bounded_compact_history_and_guards_never_load_recovery_bodies(config, inbox, monkeypatch):
    from fiber_bundle.organization import inbox_review_ledger

    client, vault, _, path, *_ = inbox
    with database.connect(config.state_root) as db:
        prepared = plan(config, db, inbox)
        value = plans.load(config, db, client, vault, prepared["operation_id"])
        # A large backup field is deliberately not part of the history projection.
        value["before"] = "not-a-recovery-body-to-decode" * 10000
        store.put(config, plans.TABLE, value["id"], vault, value)

        def forbidden(*args, **kwargs):
            raise AssertionError("full ledger rows must not be materialized")

        monkeypatch.setattr(store, "rows", forbidden)
        compact = inbox_review_ledger.history(config, vault, client)
        assert "before" not in compact[0] and "locations_before" not in compact[0]
        assert plans.history(config, db, client, vault)["operations"][0]["can_restore"] is False
        assert not inbox_review_ledger.pending(config, vault, path)
        assert not inbox_review_ledger.quarantined(config, vault, {path}, "intake", str(uuid4()))


def test_partial_scope_diagnostics_quarantine_and_revoked_history(config, inbox):
    client, vault, root, path, identity, _ = inbox
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            (json.dumps({"include": ["Inbox"], "exclude": []}), vault),
        )
        assert (
            review.inspect(config, db, client, vault, identity, path)["classification"] == "empty"
        )
        prepared = plan(config, db, inbox)
        apply(config, db, inbox, prepared)
        assert plans.history(config, db, client, vault)["operations"]
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            (json.dumps({"include": ["Inbox"], "exclude": [path]}), vault),
        )
        assert plans.history(config, db, client, vault)["operations"] == []
        with pytest.raises(auth.Denied):
            plans.restore_preview(config, db, client, vault, prepared["operation_id"])
    assert not (root / path).exists()


def test_inline_inspect_preview_apply_never_runs_full_namespace_census(config, inbox, monkeypatch):
    from fiber_bundle.archive_link_planning import LinkPlanningSession

    client, vault, root, path, identity, _ = inbox
    (root / path).write_text("图: [110](上) [111](下)\n")
    report(config, inbox, [(1, "上"), (1, "下")])

    def forbidden(*args, **kwargs):
        raise AssertionError("inline links must not scan the whole Vault")

    monkeypatch.setattr(LinkPlanningSession, "candidates", forbidden)
    with database.connect(config.state_root) as db:
        current = review.inspect(config, db, client, vault, identity, path)
        assert len(current["links"]) == 2
        prepared = plan(config, db, inbox, "unlink_missing", [r["id"] for r in current["links"]])
        assert apply(config, db, inbox, prepared)["state"] == "completed"


@pytest.mark.parametrize("crash_after_exchange", [False, True])
def test_exchange_concurrent_latest_body_has_exact_explicit_restore_preview(
    config, inbox, monkeypatch, crash_after_exchange
):
    client, vault, root, path, identity, _ = inbox
    (root / path).write_text("[110](上)\n")
    report(config, inbox, [(1, "上")])
    actual = file_exchange.rename
    latest = "人工最新正文\n新一行 [110](上)\n"

    def racing_exchange(*args, **kwargs):
        (root / path).write_text(latest)
        actual(*args, **kwargs)
        if crash_after_exchange:
            raise RuntimeError("synthetic crash immediately after concurrent exchange")

    with database.connect(config.state_root) as db:
        link = review.inspect(config, db, client, vault, identity, path)["links"][0]
        prepared = plan(config, db, inbox, "unlink_missing", [link["id"]])
        monkeypatch.setattr(file_exchange, "rename", racing_exchange)
        if crash_after_exchange:
            with pytest.raises(RuntimeError, match="synthetic crash"):
                apply(config, db, inbox, prepared)
            monkeypatch.setattr(file_exchange, "rename", actual)
        with pytest.raises(IntegrityError, match="concurrent_edit_preserved"):
            apply(config, db, inbox, prepared)
        history = plans.history(config, db, client, vault)["operations"][0]
        assert history["can_restore"] and not history["can_reconcile"]
        with pytest.raises(IntegrityError, match="restore_required"):
            apply(config, db, inbox, prepared)
        monkeypatch.setattr(file_exchange, "rename", actual)
        restoring = plans.restore_preview(config, db, client, vault, prepared["operation_id"])
        assert restoring["changes"] == [
            {"line": 1, "before": r"\[110\](上)", "after": "人工最新正文"},
            {"line": 2, "before": "", "after": "新一行 [110](上)"},
        ]
        assert apply(config, db, inbox, restoring)["state"] == "completed"
        assert (root / path).read_text() == latest


@pytest.mark.parametrize("target_kind", ["file", "directory", "symlink", "out_of_scope"])
def test_inline_existing_or_denied_targets_never_offered(config, inbox, target_kind):
    client, vault, root, path, identity, _ = inbox
    target = root / "Inbox/上"
    if target_kind == "file":
        target.write_text("real target")
    elif target_kind == "directory":
        target.mkdir()
    elif target_kind == "symlink":
        target.symlink_to(root / path)
    (root / path).write_text("[110](上)\n")
    report(config, inbox, [(1, "上")])
    with database.connect(config.state_root) as db:
        if target_kind == "out_of_scope":
            db.execute(
                "UPDATE vaults SET scope_json=? WHERE id=?",
                (json.dumps({"include": [path], "exclude": []}), vault),
            )
        assert review.inspect(config, db, client, vault, identity, path)["links"] == []


def test_nested_restore_race_only_excludes_proven_ancestors_and_settles_chain(
    config, inbox, monkeypatch
):
    from fiber_bundle.organization import inbox_review_ledger

    client, vault, root, path, identity, _ = inbox
    (root / path).write_text("[110](上)\n")
    report(config, inbox, [(1, "上")])
    actual = file_exchange.rename
    latest = ["latest human one\n", "latest human two\n"]

    def race(*args, **kwargs):
        (root / path).write_text(latest.pop(0))
        actual(*args, **kwargs)

    with database.connect(config.state_root) as db:
        link = review.inspect(config, db, client, vault, identity, path)["links"][0]
        original = plan(config, db, inbox, "unlink_missing", [link["id"]])
        monkeypatch.setattr(file_exchange, "rename", race)
        with pytest.raises(IntegrityError, match="concurrent_edit_preserved"):
            apply(config, db, inbox, original)
        first = plans.restore_preview(config, db, client, vault, original["operation_id"])
        with pytest.raises(IntegrityError, match="concurrent_edit_preserved"):
            apply(config, db, inbox, first)
        monkeypatch.setattr(file_exchange, "rename", actual)
        second = plans.restore_preview(config, db, client, vault, first["operation_id"])
        unrelated = copy.deepcopy(plans.load(config, db, client, vault, original["operation_id"]))
        unrelated["id"] = str(uuid4())
        plans.save(config, unrelated, "reconcile")
        with pytest.raises(IntegrityError, match="reconciliation_required"):
            apply(config, db, inbox, second)
        plans.save(config, unrelated, "completed")  # Synthetic unrelated owner has settled.
        assert apply(config, db, inbox, second)["state"] == "completed"
        assert (root / path).read_text() == "latest human two\n"
        for ancestor in (original, first):
            item = plans.load(config, db, client, vault, ancestor["operation_id"])
            assert item["state"] == "completed" and item["restored_by"] == second["operation_id"]
        assert not inbox_review_ledger.pending(config, vault, path)
