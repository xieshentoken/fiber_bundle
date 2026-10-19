import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from test_query_routing_preparation import routing as routing
from test_session_search import intake
from test_session_search import owner as owner

import fiber_bundle
from fiber_bundle import answer_tasks, auth, database, local_query_tasks
from fiber_bundle import document_creation_contract as contract
from fiber_bundle import document_creation_ledger as ledger
from fiber_bundle import document_creation_tasks as tasks
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.inputs import canonical
from fiber_bundle.query_routing_config import parse


@pytest.fixture
def input_task(http, owner, config, routing):
    receipt = intake(http, owner)
    data = answer_tasks.AnswerInput(
        receipt["job_id"], "current", "What pressure?", "psi", submission_id="document-input"
    )
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            result = local_query_tasks.submit(config, db, parse(routing), owner[0], data)
        finally:
            db.close()
    return result["task_id"], data


@pytest.fixture
def working(config, input_task):
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            yield db, input_task[0]
        finally:
            db.close()


def submit(config, db, client_id, tid, name="document-one"):
    return tasks.submit(config, db, client_id, name, tid, "制作汇报\n保持原文。", "md")


def amend(config, db, client_id, task, addition="增加结论。"):
    return tasks.amend(
        config,
        db,
        client_id,
        task["task_id"],
        addition,
        expected_revision=task["revision"],
        expected_sha256=task["plan_sha256"],
    )


def cancel(config, db, client_id, task):
    return tasks.cancel(
        config,
        db,
        client_id,
        task["task_id"],
        expected_revision=task["revision"],
        expected_sha256=task["plan_sha256"],
    )


def rows(config):
    with ledger.connection(config) as db:
        assert db is not None
        return {
            t: [tuple(r) for r in db.execute("SELECT * FROM " + t + " ORDER BY rowid")]
            for t in ["tasks", "versions", "events"]
        }


def test_durable_prepared_task_has_no_primary_mutation_provider_or_execution(
    config, owner, working
):
    db, tid = working
    before = list(db.iterdump())
    wire = (config.state_root / "metadata.sqlite3").read_bytes()
    with (
        patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("provider")),
        patch("fiber_bundle.execution_config.read_secret", side_effect=AssertionError("secret")),
        patch("subprocess.run", side_effect=AssertionError("executor")),
        patch("socket.create_connection", side_effect=AssertionError("network")),
    ):
        first = submit(config, db, owner[0], tid)
        initial = tasks.plan(config, db, owner[0], first["task_id"])
        second = amend(config, db, owner[0], first)
        restored = tasks.plan(config, db, owner[0], first["task_id"])
        assert tasks.plan(config, db, owner[0], first["task_id"], 1) == initial
        assert restored.input_json == initial.input_json
        assert restored.requirements == (*initial.requirements, "增加结论。")
    assert first["state"] == second["state"] == "prepared"
    assert second["revision"] == 2 and second["input_state"] == "current"
    assert (
        second["execution_allowed"] is second["delivery_allowed"] is second["save_allowed"] is False
    )
    assert "100 psi" not in canonical(second)
    assert "制作汇报" not in canonical(second)
    assert list(db.iterdump()) == before
    assert (config.state_root / "metadata.sqlite3").read_bytes() == wire
    path = config.state_root / "document-creation.sqlite3"
    assert path.stat().st_mode & 0o777 == 0o600
    with ledger.connection(config) as sidecar:
        assert sidecar.execute("PRAGMA user_version").fetchone()[0] == 1
        assert sidecar.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not sidecar.execute("PRAGMA foreign_key_check").fetchall()
    assert [len(v) for v in rows(config).values()] == [1, 2, 2]


def test_idempotent_submit_keeps_original_binding_after_amend_and_cancel(config, owner, working):
    db, tid = working
    first = submit(config, db, owner[0], tid)
    current = amend(config, db, owner[0], first)
    before = rows(config)
    assert submit(config, db, owner[0], tid) == current
    assert rows(config) == before
    stopped = cancel(config, db, owner[0], current)
    before = rows(config)
    assert submit(config, db, owner[0], tid) == stopped
    assert cancel(config, db, owner[0], stopped) == stopped
    assert rows(config) == before
    with pytest.raises(InvalidInput, match="not_prepared"):
        amend(config, db, owner[0], stopped)
    with pytest.raises(InvalidInput, match="not_prepared"):
        tasks.plan(config, db, owner[0], stopped["task_id"])


@pytest.mark.parametrize("change", ["requirements", "format", "task"])
def test_submission_conflict_preserves_every_record(config, owner, working, change):
    db, tid = working
    submit(config, db, owner[0], tid)
    before = rows(config)
    with pytest.raises(InvalidInput, match="submission_conflict"):
        tasks.submit(
            config,
            db,
            owner[0],
            "document-one",
            "another" if change == "task" else tid,
            "other" if change == "requirements" else "制作汇报\n保持原文。",
            "docx" if change == "format" else "md",
        )
    assert rows(config) == before


@pytest.mark.parametrize("change", ["grant", "session", "source", "input_cancel"])
def test_changed_input_blocks_content_and_revision_but_allows_owner_cancel(
    config, owner, working, change
):
    db, tid = working
    task = submit(config, db, owner[0], tid)
    before = rows(config)
    if change == "grant":
        auth.set_grant(db, owner[0], owner[2]["personal"], query=False, original=False, add=True)
    elif change == "session":
        db.execute("DELETE FROM session_refs")
    elif change == "source":
        (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 90 psi")
    else:
        db.execute("UPDATE local_query_tasks SET state='cancelled' WHERE id=?", (tid,))
    view = tasks.status(config, db, owner[0], task["task_id"])
    assert view["input_state"] != "current" and view["error_code"] is not None
    for call in [
        lambda: tasks.plan(config, db, owner[0], task["task_id"]),
        lambda: amend(config, db, owner[0], task),
    ]:
        with pytest.raises((auth.Denied, IntegrityError, InvalidInput)):
            call()
    assert rows(config) == before
    assert cancel(config, db, owner[0], task)["state"] == "cancelled"


def test_other_and_revoked_clients_cannot_read_or_mutate(config, owner, working):
    db, tid = working
    task = submit(config, db, owner[0], tid)
    other = auth.create_client(db, "other", False)["id"]
    before = rows(config)
    for cid in [other, owner[0]]:
        if cid == owner[0]:
            auth.revoke_client(db, cid)
        for call in [
            lambda cid=cid: tasks.status(config, db, cid, task["task_id"]),
            lambda cid=cid: tasks.history(config, db, cid, task["task_id"]),
            lambda cid=cid: tasks.plan(config, db, cid, task["task_id"]),
            lambda cid=cid: amend(config, db, cid, task),
            lambda cid=cid: cancel(config, db, cid, task),
        ]:
            with pytest.raises(auth.Denied):
                call()
    assert rows(config) == before


def test_missing_or_ungranted_input_never_creates_a_ledger(config, owner, working):
    db, tid = working
    path = config.state_root / "document-creation.sqlite3"
    for call in [
        lambda: tasks.status(config, db, owner[0], "missing"),
        lambda: tasks.amend(
            config, db, owner[0], "missing", "new", expected_revision=1, expected_sha256="0" * 64
        ),
        lambda: tasks.cancel(
            config, db, owner[0], "missing", expected_revision=1, expected_sha256="0" * 64
        ),
        lambda: submit(config, db, owner[0], "missing"),
    ]:
        with pytest.raises(auth.Denied):
            call()
        assert not path.exists()
    auth.set_grant(db, owner[0], owner[2]["personal"], query=False, original=False, add=True)
    with pytest.raises(auth.Denied):
        submit(config, db, owner[0], tid)
    assert not path.exists()


def test_stale_revision_cannot_amend_or_cancel(config, owner, working):
    db, tid = working
    first = submit(config, db, owner[0], tid)
    amend(config, db, owner[0], first)
    before = rows(config)
    for call in [
        lambda: amend(config, db, owner[0], first),
        lambda: cancel(config, db, owner[0], first),
    ]:
        with pytest.raises(InvalidInput, match="plan_conflict"):
            call()
    assert rows(config) == before


def test_audit_insertion_failure_rolls_back_pointer_and_new_version(config, owner, working):
    db, tid = working
    first = submit(config, db, owner[0], tid)
    with ledger.connection(config, write=True) as sidecar:
        sidecar.execute(
            "CREATE TRIGGER reject_amend BEFORE INSERT ON events "
            "WHEN NEW.action='amended' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    before = rows(config)
    with pytest.raises(sqlite3.IntegrityError):
        amend(config, db, owner[0], first)
    assert rows(config) == before
    with ledger.connection(config, write=True) as sidecar:
        sidecar.execute("DROP TRIGGER reject_amend")
    assert amend(config, db, owner[0], first)["revision"] == 2


def test_initial_submission_failure_leaves_no_partial_task(config, owner, working):
    db, tid = working
    with ledger.connection(config, write=True, create=True) as sidecar:
        sidecar.execute(
            "CREATE TRIGGER reject_submit BEFORE INSERT ON events "
            "WHEN NEW.action='submitted' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        submit(config, db, owner[0], tid)
    assert all(not values for values in rows(config).values())
    with ledger.connection(config, write=True) as sidecar:
        sidecar.execute("DROP TRIGGER reject_submit")
    assert submit(config, db, owner[0], tid)["revision"] == 1


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        "[]",
        "NaN",
        '{"requirements":[],"requirements":[]}',
        "```json\n{}\n```",
        "x" * (contract.MAX_PLAN_BYTES + 1),
    ],
)
def test_invalid_stored_plan_wire_is_rejected(raw):
    with pytest.raises(InvalidInput, match="plan_invalid"):
        contract.parse(raw)


@pytest.mark.parametrize("damage", ["head", "plan", "gate", "parent", "event", "missing_version"])
def test_corrupt_head_version_or_audit_is_not_accepted(config, owner, working, damage):
    db, tid = working
    first = submit(config, db, owner[0], tid)
    amend(config, db, owner[0], first)
    with ledger.connection(config, write=True) as sidecar:
        if damage == "head":
            sidecar.execute("UPDATE tasks SET head_sha256=?", ("0" * 64,))
        elif damage in ["plan", "gate", "parent"]:
            raw = sidecar.execute("SELECT plan_json FROM versions WHERE revision=2").fetchone()[0]
            value = json.loads(raw)
            if damage == "plan":
                value["requirements"][-1] = "tampered"
            elif damage == "gate":
                value["execution_allowed"] = True
            else:
                value["parent_sha256"] = "0" * 64
            sidecar.execute("UPDATE versions SET plan_json=? WHERE revision=2", (canonical(value),))
        elif damage == "event":
            sidecar.execute("DELETE FROM events WHERE action='amended'")
        else:
            sidecar.execute("DELETE FROM events WHERE revision=2")
            sidecar.execute("DELETE FROM versions WHERE revision=2")
    with pytest.raises(IntegrityError, match="ledger_invalid"):
        tasks.status(config, db, owner[0], first["task_id"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_allowed", True),
        ("execution_allowed", 0),
        ("unverified_draft", 1),
        ("schema", "unknown"),
        ("extra", True),
    ],
)
def test_plan_reload_rejects_changed_or_coerced_derived_fields(
    config, owner, working, field, value
):
    db, tid = working
    task = submit(config, db, owner[0], tid)
    plan = tasks.plan(config, db, owner[0], task["task_id"])
    assert contract.parse(canonical(plan.value())) == plan
    data = plan.value()
    data[field] = value
    with pytest.raises(InvalidInput, match="plan_invalid"):
        contract.parse(canonical(data))


@pytest.mark.parametrize("kind", ["permissions", "version", "hardlink", "symlink"])
def test_untrusted_sidecar_is_rejected_without_overwrite(config, owner, working, kind):
    db, tid = working
    submit(config, db, owner[0], tid)
    path = config.state_root / "document-creation.sqlite3"
    if kind == "permissions":
        path.chmod(0o644)
    elif kind == "version":
        with ledger.connection(config, write=True) as sidecar:
            sidecar.execute("PRAGMA user_version=99")
    elif kind == "hardlink":
        os.link(path, config.state_root / "alias.sqlite3")
    else:
        saved = config.state_root / "saved.sqlite3"
        path.rename(saved)
        path.symlink_to(saved)
    before = path.read_bytes()
    with pytest.raises(InvalidInput if kind == "symlink" else IntegrityError):
        submit(config, db, owner[0], tid)
    assert path.read_bytes() == before


def test_task_and_requirement_limits_preserve_state(config, owner, working, monkeypatch):
    db, tid = working
    task = submit(config, db, owner[0], tid)
    monkeypatch.setattr(ledger, "MAX_TASKS", 1)
    with pytest.raises(InvalidInput, match="task_limit"):
        submit(config, db, owner[0], tid, "document-two")
    before = rows(config)
    with pytest.raises(InvalidInput):
        amend(config, db, owner[0], task, "x" * contract.MAX_REQUIREMENT_BYTES)
    assert rows(config) == before


def test_phase_cannot_be_supplied_and_schema_cannot_claim_running(config, owner, working):
    db, tid = working
    task = submit(config, db, owner[0], tid)
    before = rows(config)
    with pytest.raises(TypeError):
        tasks.amend(
            config,
            db,
            owner[0],
            task["task_id"],
            "new",
            expected_revision=1,
            expected_sha256=task["plan_sha256"],
            phase="running",
        )
    with ledger.connection(config, write=True) as sidecar:
        with pytest.raises(sqlite3.IntegrityError):
            sidecar.execute("UPDATE tasks SET state='running'")
    assert rows(config) == before


def test_two_independent_connections_accept_only_one_same_revision(config, owner, input_task):
    with database.locked(config):
        db = database.connect(config.state_root)
        task = submit(config, db, owner[0], input_task[0])
        db.close()
    barrier = threading.Barrier(2)

    def change(addition):
        barrier.wait(timeout=10)
        with database.locked(config):
            main = database.connect(config.state_root)
            try:
                try:
                    return amend(config, main, owner[0], task, addition)["revision"]
                except InvalidInput as exc:
                    return str(exc)
            finally:
                main.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(change, ["first", "second"]))
    assert sorted(map(str, result)) == ["2", "document_plan_conflict"]
    assert [len(v) for v in rows(config).values()] == [1, 2, 2]


def test_new_python_process_restores_same_head_without_replay(config, owner, input_task):
    with database.locked(config):
        db = database.connect(config.state_root)
        task = amend(config, db, owner[0], submit(config, db, owner[0], input_task[0]))
        db.close()
    code = """
import json,sys
from pathlib import Path
from fiber_bundle import database,document_creation_tasks
from fiber_bundle.config import load_config
root=Path(sys.argv[1]);config=load_config(root/'config.json',root)
with database.locked(config):
 db=database.connect(config.state_root)
 try: print(json.dumps(document_creation_tasks.status(config,db,sys.argv[2],sys.argv[3])))
 finally: db.close()
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(fiber_bundle.__file__).parent.parent)
    before = rows(config)
    result = subprocess.run(
        [sys.executable, "-c", code, str(config.project_root), owner[0], task["task_id"]],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert json.loads(result.stdout) == task
    assert rows(config) == before
