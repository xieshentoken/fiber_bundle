import json

import pytest

from fiber_bundle import cloud_files, cloud_observation, note_read_session


def test_named_failure_and_only_changed_fields_are_bounded(tmp_path):
    value = {
        "schema": "cloud-status/v1",
        "available": False,
        "errors": 0,
        "pending": 0,
        "stale": 1,
        "conflicts": 0,
        "excluded_present": 0,
        "metadata_unavailable": 0,
        "failures": [{"name": "note.md", "reason": "stale"}],
    }
    with cloud_observation.scope(tmp_path), cloud_observation.phase("moc_apply"):
        with pytest.raises(cloud_observation.ObservationError) as caught:
            cloud_files._checked_status(json.dumps(value).encode(), tmp_path, ("note.md",))
        assert caught.value.detail["path"] == "note.md"
        assert caught.value.detail["phase"] == "moc_apply"
        assert caught.value.detail["reason"] == "stale"
        assert caught.value.detail["retryable_read"]
        error = cloud_observation.changed(
            tmp_path / "note.md", [1, 2, 3, 1, 100, 6, 7], [1, 2, 3, 1, 100, 6, 8]
        )
        assert error.detail["changed_fields"] == ["ctime_ns"]
        assert str(tmp_path) not in json.dumps(error.detail)


def test_bounded_retry_is_read_only_and_per_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_observation.time, "sleep", lambda _: None)
    calls = []

    @cloud_observation.retry_read
    def read(*, note_errors):
        calls.append(1)
        if len(calls) == 1:
            note_errors["transient.md"] = "temporary"
            raise cloud_observation.ObservationError(
                "cloud_metadata_unavailable", tmp_path, "metadata_unavailable"
            )
        assert note_errors == {"frozen.md": "pending"}
        return "freshly validated"

    with note_read_session.reuse() as session:
        assert read(note_errors={"frozen.md": "pending"}) == "freshly validated"
        assert session.counts["cloud_rechecks"] == 1

        @cloud_observation.retry_read
        def again():
            calls.append(1)
            raise cloud_observation.ObservationError(
                "cloud_metadata_unavailable", tmp_path, "metadata_unavailable"
            )

        with pytest.raises(cloud_observation.ObservationError):
            again()
    assert len(calls) == 3


@pytest.mark.parametrize(
    "reason", ["conflict", "pending", "scope_changed", "io_error", "invalid_response"]
)
def test_conflict_or_unavailable_source_is_not_retried(tmp_path, reason):
    calls = []

    @cloud_observation.retry_read
    def read():
        calls.append(1)
        raise cloud_observation.ObservationError(
            "cloud_item_pending_stale_or_conflicted", tmp_path, reason
        )

    with note_read_session.reuse(), pytest.raises(cloud_observation.ObservationError):
        read()
    assert len(calls) == 1


def test_directory_diagnostic_keeps_path_phase_and_fresh_retry_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_observation.time, "sleep", lambda _: None)
    before = (1, 2, 0o40700, 2, 64, 6, 7, 0, 9)
    after = (1, 2, 0o40700, 3, 96, 8, 10, 0, 9)
    calls = []

    @cloud_observation.retry_read
    def read():
        calls.append(1)
        raise cloud_observation.directory_changed(tmp_path / "Inbox", before, after)

    with (
        cloud_observation.scope(tmp_path),
        cloud_observation.phase("archive_finalize_inventory"),
        note_read_session.reuse() as session,
        pytest.raises(cloud_observation.ObservationError) as caught,
    ):
        read()
    assert len(calls) == 2 and session.counts["cloud_rechecks"] == 1
    assert caught.value.detail["path"] == "Inbox"
    assert caught.value.detail["phase"] == "archive_finalize_inventory"
    assert caught.value.detail["changed_fields"] == ["links", "size", "mtime_ns", "ctime_ns"]
    assert caught.value.detail["retryable_read"]
    for index in (0, 1, 2, 7, 8):
        changed = list(before)
        changed[index] += 1
        assert not cloud_observation.directory_changed(tmp_path, before, tuple(changed)).detail[
            "retryable_read"
        ]
