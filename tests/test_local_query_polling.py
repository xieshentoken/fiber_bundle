"""Synthetic local execution: authority stays immediate, evidence gates every boundary."""

import os
from dataclasses import replace
from unittest.mock import patch

import pytest
from test_local_query_gateway import completion
from test_local_query_tasks import route as route
from test_local_query_worker import pi
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import answer_tasks, auth, database, note_inventory
from fiber_bundle import local_query_execution as execution
from fiber_bundle import local_query_ledger as ledger
from fiber_bundle import local_query_tasks as tasks
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.local_query_contract import prepare
from fiber_bundle.local_query_process import LocalCallStopped


@pytest.fixture
def note_input(config, owner):
    path = config.project_root / "test vault/temp_vault1/pressure.md"
    path.write_text("# Pressure\n100 psi synthetic evidence\n")
    return answer_tasks.NoteAnswerInput(
        owner[2]["personal"], "What pressure?", "psi", submission_id="polling"
    )


def submit(config, route, owner, data):
    with database.connect(config.state_root) as db:
        return tasks.submit(config, db, route, owner[0], data)["task_id"]


def change_state(config, owner, tid, change):
    root = config.project_root / "test vault/temp_vault1"
    path = root / "pressure.md"
    with database.locked(config), database.connect(config.state_root) as db:
        if change == "cancel":
            ledger.stop(config, db, owner[0], tid)
        elif change == "revoke":
            auth.set_grant(
                db, owner[0], owner[2]["personal"], query=False, original=False, add=True
            )
        elif change == "client":
            auth.revoke_client(db, owner[0])
        elif change == "root":
            root.rename(root.with_name("old-vault"))
            root.mkdir()
        elif change == "new":
            (root / "new.md").write_text("200 psi new evidence")
        elif change == "delete":
            path.unlink()
        elif change == "edit":
            path.write_text("# Pressure\n200 psi edited synthetic evidence\n")
        elif change == "same_size":
            info = path.stat()
            path.write_text(path.read_text().replace("100", "200"))
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            assert path.stat().st_size == info.st_size
            assert path.stat().st_mtime_ns == info.st_mtime_ns
        else:
            raise AssertionError(change)


def test_submit_retrieves_once_and_freezes_original_scope(config, route, owner, note_input):
    with database.connect(config.state_root) as db:
        with patch.object(answer_tasks, "retrieve", wraps=answer_tasks.retrieve) as retrieve:
            tid = tasks.submit(config, db, route, owner[0], note_input)["task_id"]
            assert retrieve.call_count == 1
            assert tasks.submit(config, db, route, owner[0], note_input)["task_id"] == tid
            assert retrieve.call_count == 1
        change_state(config, owner, tid, "edit")
        with pytest.raises(IntegrityError):
            ledger.claim(config, db, route, owner[0], tid)
        assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 0


@pytest.mark.parametrize("scoped", [False, True])
def test_root_replacement_during_first_retrieval_rolls_back(
    config, route, owner, note_input, scoped
):
    original = answer_tasks.retrieve
    root = config.project_root / "test vault/temp_vault1"
    if scoped:
        note_input = answer_tasks.ScopedNoteAnswerInput(
            (owner[2]["personal"],), "What pressure?", "psi", submission_id="scoped"
        )

    def replace_root(*args, **kwargs):
        response = original(*args, **kwargs)
        root.rename(root.with_name("old-vault"))
        root.mkdir()
        return response

    with (
        database.connect(config.state_root) as db,
        patch.object(answer_tasks, "retrieve", side_effect=replace_root) as retrieve,
    ):
        with pytest.raises(IntegrityError, match="local_vault_identity_changed"):
            tasks.submit(config, db, route, owner[0], note_input)
        assert retrieve.call_count == 1
        assert db.execute("SELECT count(*) FROM local_query_tasks").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["call_id", "digest", "call_digest", "call_state"])
def test_light_poll_checks_exact_reserved_claim(config, route, owner, note_input, change):
    tid = submit(config, route, owner, note_input)
    with database.connect(config.state_root) as db:
        owned = ledger.claim(config, db, route, owner[0], tid)
        if change == "call_id":
            owned = replace(owned, call_id="foreign-call")
        elif change == "digest":
            owned = replace(owned, configuration_sha256="a" * 64)
        elif change == "call_digest":
            db.execute("UPDATE local_query_calls SET config_digest=?", ("a" * 64,))
        else:
            db.execute("UPDATE local_query_calls SET state='uncertain'")
        with (
            patch.object(answer_tasks, "retrieve", side_effect=AssertionError("retrieval")),
            patch.object(note_inventory, "read", side_effect=AssertionError("inventory")),
            pytest.raises(InvalidInput, match="local_claim_invalid"),
        ):
            execution._active(config, owned)


def test_hundred_wait_polls_do_not_retrieve_and_boundaries_do(
    config, route, owner, note_input, tmp_path
):
    tid = submit(config, route, owner, note_input)
    polls = 0

    def complete(local, request, **options):
        nonlocal polls
        assert options["allow_inference"] is True
        with (
            patch.object(answer_tasks, "retrieve", side_effect=AssertionError("wait retrieval")),
            patch.object(note_inventory, "read", side_effect=AssertionError("wait inventory")),
        ):
            for _ in range(100):
                assert not options["should_stop"]()
                polls += 1
        return completion(prepare(local, request))

    with (
        pi(tmp_path),
        patch.object(execution.local_query_gateway, "complete", side_effect=complete) as provider,
        patch.object(answer_tasks, "retrieve", wraps=answer_tasks.retrieve) as retrieve,
    ):
        result = execution.execute(config, route, owner[0], tid)
        assert result["state"] == "succeeded"
        assert polls == 100 and provider.call_count == 1
        # Claim, immediately before send, immediately after receive and ledger finish.
        assert retrieve.call_count == 4
        with database.connect(config.state_root) as db:
            assert tasks.deliver(config, db, owner[0], tid)["model_used"] is True
        assert retrieve.call_count == 5


@pytest.mark.parametrize(
    "change", ["cancel", "revoke", "client", "root", "new", "delete", "edit", "same_size"]
)
def test_midcall_change_never_persists_or_delivers_a_result(
    config, route, owner, note_input, tmp_path, change
):
    tid = submit(config, route, owner, note_input)
    content_change = change in {"new", "delete", "edit", "same_size"}

    def complete(local, request, **options):
        output = completion(prepare(local, request))
        assert not options["should_stop"]()
        change_state(config, owner, tid, change)
        if content_change:
            # Intentional boundary: content changes do not interrupt computation;
            # the full post-receive check rejects it before reply/persistence.
            assert not options["should_stop"]()
            return output
        if change == "cancel":
            assert options["should_stop"]()
            raise LocalCallStopped()
        with pytest.raises((auth.Denied, IntegrityError)):
            options["should_stop"]()
        raise LocalCallStopped()

    with (
        pi(tmp_path),
        patch.object(execution.local_query_gateway, "complete", side_effect=complete) as provider,
    ):
        result = execution.execute(config, route, owner[0], tid)
        assert result["state"] == ("cancelled" if change == "cancel" else "failed")
        assert provider.call_count == 1
    with database.connect(config.state_root) as db:
        assert tasks.get(db, owner[0], tid)["result_json"] is None
        call = db.execute("SELECT * FROM local_query_calls WHERE task_id=?", (tid,)).fetchone()
        assert call["state"] == ("settled" if content_change else "uncertain")
        assert call["input_tokens"] == (20 if content_change else None)
        assert db.execute("SELECT count(*) FROM local_query_calls").fetchone()[0] == 1
        with pytest.raises((auth.Denied, IntegrityError, InvalidInput)):
            tasks.deliver(config, db, owner[0], tid)
        with pytest.raises(InvalidInput, match="not_replayable"):
            ledger.claim(config, db, route, owner[0], tid)


def test_content_change_after_claim_is_checked_before_provider_send(
    config, route, owner, note_input, tmp_path
):
    tid = submit(config, route, owner, note_input)
    original = execution.command

    def edit_before_start(*args, **kwargs):
        change_state(config, owner, tid, "edit")
        return original(*args, **kwargs)

    with (
        pi(tmp_path),
        patch.object(execution, "command", side_effect=edit_before_start),
        patch.object(execution.local_query_gateway, "complete") as provider,
    ):
        assert execution.execute(config, route, owner[0], tid)["state"] == "failed"
        provider.assert_not_called()
    with database.connect(config.state_root) as db:
        assert tasks.get(db, owner[0], tid)["result_json"] is None
