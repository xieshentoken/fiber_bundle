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
