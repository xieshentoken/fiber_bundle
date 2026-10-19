"""Actual owned subprocess stop and cancellation during an in-flight paid request."""

import json
import os
import threading
import time

import pytest
from test_channel_intake import records
from test_intake_abort_commands import command
from test_intake_video_skill import install_v3
from test_intake_video_skill import media as media

from fiber_bundle import database, feishu_receiver, model_gateway, video_skill
from fiber_bundle.channels import intake_extraction

pytest_plugins = ["test_channel_intake", "test_execution"]


def link_for(config, entry, batch):
    return json.loads(next(r for r in records(config, entry) if r["batch_id"] == batch)["value"])[
        "links"
    ][0]


def test_abort_stops_real_owned_asr_subprocess(config, entry, policy, monkeypatch, tmp_path, media):
    actual_run = video_skill.run
    batch, _ = install_v3(config, entry, policy, monkeypatch, tmp_path, media, subtitles=False)
    (tmp_path / "skill/scripts/run_asr.py").write_text(
        "import os,sys,time\nfrom pathlib import Path\n"
        "(Path(sys.argv[1])/'asr-running.pid').write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    transport = video_skill.run

    def controlled(config, binding, job, script, args, guard, **kwargs):
        return (actual_run if script == "run_asr" else transport)(
            config, binding, job, script, args, guard, **kwargs
        )

    monkeypatch.setattr(video_skill, "run", controlled)
    errors = []

    def extract():
        try:
            intake_extraction.advance(config, entry[0]["id"])
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=extract)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        marker = None
        while time.monotonic() < deadline:
            markers = list((config.state_root / "video-skill-jobs").glob("*/asr-running.pid"))
            if markers:
                marker = markers[0]
                break
            time.sleep(0.05)
        assert marker is not None, errors
        pid = int(marker.read_text())
        task = link_for(config, entry, batch)["video_processing"]["job_id"]
        assert command(config, entry, "abort " + task)["state"] == "intake_abort_recorded"
        feishu_receiver.advance(config, entry[1])
    finally:
        thread.join(timeout=10)
    assert not thread.is_alive() and not errors, errors
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    link = link_for(config, entry, batch)
    assert link["state"] == "cancelled" and not link.get("publication")
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


def test_abort_during_model_reply_keeps_real_usage_but_never_publishes(
    config, entry, policy, monkeypatch, tmp_path, media
):
    batch, _ = install_v3(config, entry, policy, monkeypatch, tmp_path, media)
    provider = model_gateway.complete

    def reply(policy, rid, payload):
        task = link_for(config, entry, batch)["video_processing"]["job_id"]
        command(config, entry, "abort " + task)
        return provider(policy, rid, payload)

    monkeypatch.setattr(model_gateway, "complete", reply)
    assert intake_extraction.advance(config, entry[0]["id"])
    feishu_receiver.advance(config, entry[1])
    link = link_for(config, entry, batch)
    assert link["state"] == "cancelled" and not link.get("publication")
    with database.connect(config.state_root) as db:
        calls = db.execute("SELECT state,input_tokens,output_tokens FROM model_calls").fetchall()
        assert [tuple(row) for row in calls] == [("settled", 20, 20)]
