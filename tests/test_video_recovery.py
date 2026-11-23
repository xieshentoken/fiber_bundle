from types import SimpleNamespace

import pytest

from fiber_bundle.config import InvalidInput
from fiber_bundle.video_recovery import refresh


def test_explicit_refresh_preserves_original_snapshot(tmp_path):
    config = SimpleNamespace(state_root=tmp_path)
    old = {"job_id": "safe-job", "completed": [], "model_sha256": "original"}
    link = {"video_processing": old}
    refresh(config, link)
    assert "video_processing" not in link
    assert link["previous_video_processing"] == [old]


@pytest.mark.parametrize(
    "field", ["completed", "source_resolution", "vision_runs", "note_runs", "asr_runs"]
)
def test_refresh_rejects_existing_work(tmp_path, field):
    config = SimpleNamespace(state_root=tmp_path)
    old = {"job_id": "safe-job", field: ["existing"]}
    link = {"video_processing": old}
    with pytest.raises(InvalidInput, match="refresh_not_safe"):
        refresh(config, link)
    assert link == {"video_processing": old}


def test_refresh_rejects_job_directory_even_without_checkpoint(tmp_path):
    (tmp_path / "video-skill-jobs" / "safe-job").mkdir(parents=True)
    config = SimpleNamespace(state_root=tmp_path)
    with pytest.raises(InvalidInput, match="refresh_not_safe"):
        refresh(config, {"video_processing": {"job_id": "safe-job"}})
