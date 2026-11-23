from unittest.mock import patch

import pytest
from test_execution_leases import confirmed
from test_jobs import ready as ready

from fiber_bundle import note_index
from fiber_bundle.organization import execution, store
from fiber_bundle.organization.execution_reads import ExecutionReads


def test_adjacent_units_reuse_bytes_and_complete_without_replaying(config, ready):
    from test_note_moc_locations import managed

    root = ready[2]
    for n in range(40):
        (root / f"Resource/existing-{n}.md").write_text("# Existing\nunchanged")
    managed(config, ready)
    job = confirmed(config, ready, two=True)
    reads = ExecutionReads()
    for _ in range(2):
        execution.run(config, job["id"], reads=reads)
    result = store.get(config, "jobs", job["id"])
    assert result["state"] == "completed", result.get("error")
    assert result["completed"] == 2 and not result["model_called"]
    assert result["read_work"]["body_reads"] < 55
    assert reads.session is None
    before = {p: p.read_bytes() for p in root.rglob("*.md")}
    execution.run(config, job["id"], reads=reads)
    assert before == {p: p.read_bytes() for p in root.rglob("*.md")}


@pytest.mark.parametrize("replacement", [False, True])
def test_retained_bytes_never_hide_a_changed_remaining_note(config, ready, replacement):
    job = confirmed(config, ready, two=True)
    reads = ExecutionReads()
    execution.run(config, job["id"], reads=reads)
    partial = store.get(config, "jobs", job["id"])
    remaining = next(i for i in partial["items"] if i["state"] == "pending")
    path = ready[2] / remaining["path"]
    if replacement:
        temporary = path.with_suffix(".replacement")
        temporary.write_text("# Human replacement\n")
        temporary.replace(path)
    else:
        path.write_text("# Human edit\n")
    expected = path.read_bytes()
    execution.run(config, job["id"], reads=reads)
    result = store.get(config, "jobs", job["id"])
    assert result["state"] == "waiting_file"
    waiting = next(i for i in result["items"] if i["path"] == remaining["path"])
    assert waiting["wait"]["reason"] == "changed"
    assert result["completed"] == 1
    assert path.read_bytes() == expected
    assert reads.session is None


def test_scopes_discard_on_generation_idle_and_failure_but_always_recheck_cloud(config, tmp_path):
    reads = ExecutionReads()
    job = dict(id="job", vault_id="vault", client_id="client", state="running", stage="applying")
    path = tmp_path / "note.md"
    path.write_text("# A")
    available = []
    for _ in range(2):
        with reads.scope(config, job):
            note_index.read_note(tmp_path, path.name, 1000, availability=available.append)
    assert available == [path, path]
    assert reads.session.counts["body_reads"] == 1
    job["generation"] = 1
    with reads.scope(config, job):
        assert not reads.session.notes
    with patch(
        "fiber_bundle.organization.execution_reads.time.monotonic", return_value=reads.used_at + 61
    ):
        reads.expire()
    assert reads.session is None
