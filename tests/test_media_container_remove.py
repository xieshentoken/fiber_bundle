import subprocess
from unittest.mock import patch

import pytest
from test_media_jobs import media_scope as media_scope

from fiber_bundle.config import InvalidInput
from fiber_bundle.media_container_remove import remove


def test_concurrent_removal_is_reconciled_without_another_mutation():
    identity = "a" * 64
    with (
        patch(
            "fiber_bundle.media_container_remove.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1),
        ) as mutation,
        patch(
            "fiber_bundle.media_container_remove.subprocess.check_output",
            side_effect=[identity.encode(), b""],
        ) as read,
        patch("fiber_bundle.media_container_remove.time.sleep"),
    ):
        remove(identity)
    assert mutation.call_count == 1 and read.call_count == 2
    assert mutation.call_args.args[0] == ["docker", "rm", "--force", identity]


def test_unresolved_removal_is_not_retried():
    identity = "b" * 64
    with (
        patch(
            "fiber_bundle.media_container_remove.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1),
        ) as mutation,
        patch(
            "fiber_bundle.media_container_remove.subprocess.check_output",
            return_value=identity.encode(),
        ),
        patch("fiber_bundle.media_container_remove.time.monotonic", side_effect=[0, 4]),
    ):
        with pytest.raises(InvalidInput, match="unresolved"):
            remove(identity)
    assert mutation.call_count == 1


def test_invalid_identity_cannot_reach_docker():
    with patch("fiber_bundle.media_container_remove.subprocess.run") as run:
        with pytest.raises(InvalidInput):
            remove("another-container-name")
    run.assert_not_called()


@pytest.mark.parametrize("kind", ["processing", "link"])
@pytest.mark.parametrize("mismatch", [None, "image", "label", "mount"])
def test_remove_only_after_exact_scope_check(config, media_scope, kind, mismatch):
    import json
    from uuid import uuid4

    from fiber_bundle import media_link_worker, media_processing_worker

    worker = media_processing_worker if kind == "processing" else media_link_worker
    job = str(uuid4())
    resources = None
    if kind == "processing":
        from test_media_processing import RESOURCE, ready

        from fiber_bundle import media_jobs, media_processing

        job = ready(config, media_scope)
        media_processing.create(config, media_scope[0]["id"], job, RESOURCE)
        with media_jobs.connection(config) as db:
            resources = json.loads(media_processing.get(db, job)["plan_json"])
    root = worker.directory(config, job)
    image = resources["policy"]["image"] if resources else "sha256:" + "b" * 64
    info = {
        "Id": "a" * 64,
        "Config": {"Image": image, "Labels": {"fiber-bundle." + kind: job}},
        "Mounts": [
            {"Destination": "/media/input", "Source": str(root / "input"), "RW": False},
            {"Destination": "/media/output", "Source": str(root / "output"), "RW": True},
        ],
    }
    if mismatch == "image":
        info["Config"]["Image"] = "sha256:" + "c" * 64
    elif mismatch == "label":
        info["Config"]["Labels"] = {}
    elif mismatch == "mount":
        info["Mounts"][0]["RW"] = True
    with (
        patch.object(
            worker.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps([info]).encode()),
        ),
        patch("fiber_bundle.media_container_remove.remove") as deletion,
    ):
        if mismatch:
            with pytest.raises(InvalidInput, match="stop_scope"):
                worker.stop_owned(config, resources if kind == "processing" else image, job)
            deletion.assert_not_called()
        else:
            worker.stop_owned(config, resources if kind == "processing" else image, job)
            deletion.assert_called_once_with("a" * 64)


@pytest.mark.parametrize("kind", ["processing", "link"])
def test_inspect_racing_with_removal_only_waits_for_absence(config, media_scope, kind):
    import json

    from test_media_processing import RESOURCE, ready

    from fiber_bundle import (
        media_jobs,
        media_link_worker,
        media_processing,
        media_processing_worker,
    )

    job = ready(config, media_scope)
    media_processing.create(config, media_scope[0]["id"], job, RESOURCE)
    with media_jobs.connection(config) as db:
        spec = json.loads(media_processing.get(db, job)["plan_json"])
    worker = media_processing_worker if kind == "processing" else media_link_worker
    with (
        patch.object(worker.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)),
        patch(
            "fiber_bundle.media_container_remove.subprocess.check_output",
            side_effect=[b"removing", b""],
        ),
        patch("fiber_bundle.media_container_remove.time.sleep"),
        patch("fiber_bundle.media_container_remove.remove") as mutation,
    ):
        worker.stop_owned(config, spec if kind == "processing" else spec["policy"]["image"], job)
    mutation.assert_not_called()
