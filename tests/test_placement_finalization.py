"""Post-commit recovery recensuses safely and never re-enters a file write primitive."""

import hashlib
import json
import os
from unittest.mock import patch

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_moc_placement_projection import setup

from fiber_bundle import (
    auth,
    cloud_observation,
    database,
    moc_placement_projection,
    note_index,
    note_placement,
    note_placement_execution,
    partial_notes,
)
from fiber_bundle.config import IntegrityError


def planned(config, db, policy, article_scope, *, directory_case=None):
    mid, _, note, moc, identity = setup(config, policy, article_scope, True, True)
    if directory_case == "hidden_metadata_time":
        (moc.parent / ".sync-metadata").write_text("private bookkeeping")
    elif directory_case == "nested_directory_time":
        nested = moc.parent / "Recensus"
        nested.mkdir()
        (nested / "separate.md").write_text("# Separate note")
    packet = note_placement.preview(
        config, db, *article_scope[:2], identity, hashlib.sha256(note.read_bytes()).hexdigest()
    )
    return packet, note, moc


@pytest.mark.parametrize(
    "change",
    [
        "directory_time",
        "repeated_directory_time",
        "hidden_metadata_time",
        "nested_directory_time",
        "hidden",
        "visible",
        "edit",
        "replace",
        "revoke",
    ],
)
def test_finalizer_fresh_census_retains_original_cas_contract(
    config, policy, article_scope, monkeypatch, change
):
    monkeypatch.setattr(cloud_observation.time, "sleep", lambda _: None)
    with database.connect(config.state_root) as db:
        packet, note, moc = planned(config, db, policy, article_scope, directory_case=change)
        root = moc.parent
        metadata_file = root / ".sync-metadata"
        nested = root / "Recensus"
        original_read = partial_notes.read
        original_note = note_index.read_note
        visits = []
        injected = []

        def mutate(vault, path, limit, **kwargs):
            result = original_note(vault, path, limit, **kwargs)
            if change == "hidden_metadata_time":
                for changed in (metadata_file, root):
                    info = changed.stat()
                    os.utime(changed, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
                injected.append(path)
            elif change == "nested_directory_time" and path == "Recensus/separate.md":
                for changed in (nested, root):
                    info = changed.stat()
                    os.utime(changed, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
                injected.append(path)
            elif change == "repeated_directory_time":
                info = root.stat()
                os.utime(root, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
                injected.append(path)
            elif not injected:
                injected.append(path)
                if change == "hidden":
                    (root / ".sync-metadata").write_text("private filesystem bookkeeping")
                elif change == "directory_time":
                    info = root.stat()
                    os.utime(root, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
                elif change == "visible":
                    (root / "unexpected.md").write_text("# New inbound link\n[[Archive/anything]]")
                elif change == "edit":
                    moc.write_text(moc.read_text() + "\nHuman edit\n")
                elif change == "replace":
                    replacement = root / ".replacement"
                    replacement.write_bytes(moc.read_bytes())
                    replacement.replace(moc)
                elif change == "revoke":
                    auth.set_grant(db, *article_scope[:2], query=True, original=True, add=False)
            return result

        def read(*args, **kwargs):
            if kwargs.get("finalizing_operation"):
                visits.append(1)
                with patch.object(note_index, "read_note", mutate):
                    return original_read(*args, **kwargs)
            return original_read(*args, **kwargs)

        with patch.object(partial_notes, "read", read):
            if change in {
                "directory_time",
                "repeated_directory_time",
                "hidden",
                "hidden_metadata_time",
                "nested_directory_time",
            }:
                result = note_placement_execution.apply(
                    config, db, *article_scope[:2], packet["operation_id"]
                )
                assert result["state"] == "completed"
            else:
                with pytest.raises((IntegrityError, auth.Denied)):
                    note_placement_execution.apply(
                        config, db, *article_scope[:2], packet["operation_id"]
                    )
                assert (
                    db.execute(
                        "SELECT state FROM vault_operations WHERE id=?", (packet["operation_id"],)
                    ).fetchone()[0]
                    == "reconcile"
                )
        assert injected and visits == [1]  # Retry stays inside the original read-only call.
        assert not note.exists() and (root / packet["target_path"]).exists()


@pytest.mark.parametrize("failure", ["before_journal", "after_journal"])
def test_committed_resume_never_calls_exchange_move_or_metadata_commit(
    config, policy, article_scope, failure
):
    with database.connect(config.state_root) as db:
        packet, note, moc = planned(config, db, policy, article_scope)
        original = moc_placement_projection.finalize

        def interrupted(*args):
            if failure == "after_journal":
                original(*args)
            raise OSError("synthetic finalization interruption")

        with patch.object(moc_placement_projection, "finalize", interrupted):
            with pytest.raises(OSError):
                note_placement_execution.apply(
                    config, db, *article_scope[:2], packet["operation_id"]
                )
        row = db.execute(
            "SELECT payload FROM vault_operations WHERE id=?", (packet["operation_id"],)
        ).fetchone()
        assert json.loads(row[0])["metadata_committed"]
        before = moc.read_bytes(), (moc.parent / packet["target_path"]).stat().st_ino
    # Open a new connection and discard every read cache, like a restarted worker.
    with database.connect(config.state_root) as db:
        with (
            patch.object(
                note_placement_execution, "exchange", side_effect=AssertionError("exchange replay")
            ),
            patch.object(
                note_placement_execution, "move", side_effect=AssertionError("move replay")
            ),
            patch.object(
                note_placement_execution, "complete", side_effect=AssertionError("metadata replay")
            ),
        ):
            result = note_placement_execution.apply(
                config, db, *article_scope[:2], packet["operation_id"], reconcile=True
            )
            assert result["state"] == "completed"
            assert (
                note_placement_execution.apply(
                    config, db, *article_scope[:2], packet["operation_id"], reconcile=True
                )["state"]
                == "completed"
            )
        assert before == (moc.read_bytes(), (moc.parent / packet["target_path"]).stat().st_ino)
