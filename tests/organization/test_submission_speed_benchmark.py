"""Opt-in real HTTP preparation/lease measurements over deterministic temporary inputs."""

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from test_execution_leases import client
from test_jobs import ready as ready

from fiber_bundle import database, note_placement


@pytest.mark.skipif(
    not os.environ.get("FIBER_SUBMISSION_BENCHMARK"), reason="explicit benchmark only"
)
def test_submission_lease_measurement(config, ready):
    cid, vid, root, *_ = ready
    for number in range(100):
        (root / f"Inbox/heat-{number}.md").write_text(
            f"# 传热 {number}\n#热管理\n" + "傅里叶定律与传热模型。" * 100
        )
    for number in range(12):
        (root / f"Map/主题-{number} MOC.md").write_text(
            f"---\ntype: moc\n---\n# 主题 {number} MOC\n" + f"独立主题{number}。" * 100
        )
    http = client(config)
    held = []
    original = database.locked

    @contextmanager
    def measured(config):
        with original(config):
            started = time.monotonic()
            try:
                yield
            finally:
                held.append(time.monotonic() - started)

    started = time.monotonic()
    with patch.object(database, "locked", measured):
        response = http.post(
            "/admin/organization/maintenance",
            json={"client_id": cid, "vault_id": vid, "mode": "quick"},
        )
    elapsed = time.monotonic() - started
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 101
    assert not response.json()["model_called"]
    Path(os.environ["FIBER_SUBMISSION_BENCHMARK"]).write_text(
        json.dumps(
            {
                "loaded_source": str(note_placement.__file__),
                "total_seconds": elapsed,
                "lease_seconds": held,
                "writer_held_seconds": sum(held),
                "files": 114,
                "items": 101,
                "model_called": False,
            },
            indent=2,
        )
    )
    http.close()
