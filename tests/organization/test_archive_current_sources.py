"""Sources that acquire a backlink during preparation cannot silently escape a move."""

from unittest.mock import patch

import pytest
from test_execution_leases import confirmed
from test_jobs import ready as ready

from fiber_bundle.organization import execution, maintenance_archive, store


@pytest.mark.parametrize("change", ["new", "edit", "permission", "directory"])
def test_after_preparation_source_changes_preserve_original_and_block_move(config, ready, change):
    job = confirmed(config, ready)
    root = ready[2]
    stationary = root / "Resource/stationary.md"
    stationary.write_text("# Unrelated original\n")
    original = maintenance_archive.prepare
    preserved = None

    def raced(*args, **kwargs):
        nonlocal preserved
        result = original(*args, **kwargs)
        if change == "new":
            path = root / "Resource/new-inbound.md"
            path.write_text("[[Inbox/传热]]\n")
            preserved = path.read_bytes()
        elif change == "edit":
            stationary.write_text("# Human edit\n[[Inbox/传热]]\n")
            preserved = stationary.read_bytes()
        elif change == "permission":
            stationary.chmod(0o400)
            preserved = stationary.read_bytes()
        else:
            (root / "Resource").rename(root / "Resource-before")
            (root / "Resource").mkdir()
        return result

    with patch.object(maintenance_archive, "prepare", raced):
        execution.run(config, job["id"])
    result = store.get(config, "jobs", job["id"])
    assert result["state"] == "partial", result
    assert result["completed"] == 0
    assert ready[4].is_file() and not (root / "Resource/传热.md").exists()
    assert "placement_link_sources_changed" in result["error"]
    if change == "new":
        assert (root / "Resource/new-inbound.md").read_bytes() == preserved
    elif change in {"edit", "permission"}:
        assert stationary.read_bytes() == preserved


def test_plain_note_completion_does_not_enumerate_unused_publication_baselines(config, ready):
    from fiber_bundle import note_placement_execution, vault_scope

    job = confirmed(config, ready)
    original = note_placement_execution.complete

    def complete(*args, **kwargs):
        with patch.object(
            vault_scope, "metadata_entries", side_effect=AssertionError("unused baseline census")
        ):
            return original(*args, **kwargs)

    with patch.object(note_placement_execution, "complete", complete):
        execution.run(config, job["id"])
    result = store.get(config, "jobs", job["id"])
    assert result["state"] == "completed", result.get("error")
    assert result["completed"] == 1
    assert not ready[4].exists() and (ready[2] / "Resource/传热.md").is_file()
