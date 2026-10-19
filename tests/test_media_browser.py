"""Real bounded child-process exits and owner-scoped browser handoff checks."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from test_media_jobs import media_scope as media_scope
from test_media_links import task

from fiber_bundle import auth, media_browser, media_jobs
from fiber_bundle.config import InvalidInput


def binary(tmp_path, code):
    path = tmp_path / "synthetic-browser"
    path.write_text("#!" + str(Path(sys.executable).resolve()) + "\n" + code + "\n")
    path.chmod(0o700)
    return str(path)


@pytest.mark.parametrize("exit_code", [1, 2, 78])
def test_early_failure_is_rejected_without_retry_or_task_mutation(
    config, media_scope, tmp_path, exit_code
):
    identity = task(config, media_scope)
    fake = binary(tmp_path, f"import sys; sys.exit({exit_code})")
    real = subprocess.Popen
    with (
        patch.dict(media_browser.BROWSERS, {"chrome": fake}),
        patch.object(media_browser.subprocess, "Popen", wraps=real) as launch,
    ):
        with pytest.raises(InvalidInput, match="media_browser_launch_failed"):
            media_browser.launch(config, media_scope[0]["id"], identity, "chrome")
    assert launch.call_count == 1
    with media_jobs.connection(config) as db:
        assert (
            db.execute("SELECT state FROM media_link_tasks WHERE id=?", (identity,)).fetchone()[0]
            == "queued"
        )
        assert db.execute("SELECT count(*) FROM media_tasks").fetchone()[0] == 0


def test_zero_exit_is_only_pending_handoff_not_window_or_login_proof(config, media_scope, tmp_path):
    identity = task(config, media_scope)
    fake = binary(tmp_path, "import sys; sys.exit(0)")
    with patch.dict(media_browser.BROWSERS, {"chrome": fake}):
        value = media_browser.launch(config, media_scope[0]["id"], identity, "chrome")
    assert value["state"] == "browser_handoff_pending"
    assert (
        not value["login_verified"]
        and not value["cookies_exported"]
        and not value["publication_ready"]
    )


def test_running_child_is_not_killed_and_wait_is_bounded(config, media_scope, tmp_path):
    identity = task(config, media_scope)
    fake = binary(tmp_path, "import time; time.sleep(30)")
    real = subprocess.Popen
    children = []

    def start(*args, **kwargs):
        process = real(*args, **kwargs)
        children.append(process)
        return process

    try:
        with (
            patch.dict(media_browser.BROWSERS, {"chrome": fake}),
            patch.object(media_browser.subprocess, "Popen", side_effect=start),
        ):
            value = media_browser.launch(config, media_scope[0]["id"], identity, "chrome")
        assert value["state"] == "browser_handoff_pending" and children[0].poll() is None
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=5)


def test_missing_binary_does_not_create_profile(config, media_scope, tmp_path):
    identity = task(config, media_scope)
    with patch.dict(media_browser.BROWSERS, {"chrome": str(tmp_path / "missing")}):
        with pytest.raises(InvalidInput, match="media_browser_unavailable"):
            media_browser.launch(config, media_scope[0]["id"], identity, "chrome")
    assert not (config.state_root / "browser-profiles").exists()


@pytest.mark.parametrize("browser", ["chrome", "edge"])
def test_two_clients_have_distinct_reused_profiles_and_canonical_urls(
    config, media_scope, tmp_path, browser
):
    first = task(config, media_scope)
    with media_jobs.connection(config) as db:
        other = auth.create_client(db, "separate-browser-owner", False)
        auth.set_grant(db, other["id"], media_scope[1], add=True, query=True, original=True)
    from fiber_bundle import media_links

    second = media_links.create(
        config,
        other["id"],
        media_scope[1],
        "https://youtu.be/jNQXAC9IVRw",
        "audio_only",
        19000,
        "sha256:" + "a" * 64,
    )
    fake = binary(tmp_path, "import sys; sys.exit(0)")
    real = subprocess.Popen
    with (
        patch.dict(media_browser.BROWSERS, {browser: fake}),
        patch.object(media_browser.subprocess, "Popen", wraps=real) as launch,
    ):
        for cid, identity in [
            (media_scope[0]["id"], first),
            (other["id"], second),
            (media_scope[0]["id"], first),
        ]:
            media_browser.launch(config, cid, identity, browser)
    args = [call.args[0] for call in launch.call_args_list]
    assert args[0][1] == args[2][1] and args[0][1] != args[1][1]
    for argv, cid in zip(
        args, [media_scope[0]["id"], other["id"], media_scope[0]["id"]], strict=True
    ):
        assert argv[1] == "--user-data-dir=" + str(
            config.state_root / "browser-profiles" / browser / cid
        )
        assert set(argv[2:4]) == {"--no-first-run", "--no-default-browser-check"}
        assert len(argv) == 5
    assert args[1][-1] == "https://www.youtube.com/watch?v=jNQXAC9IVRw"


@pytest.mark.parametrize(
    "boundary",
    ["other_owner", "grant_revoked", "client_revoked", "cancelled", "complete", "running"],
)
def test_invalid_owner_or_stopped_task_never_spawns(config, media_scope, tmp_path, boundary):
    identity = task(config, media_scope)
    cid = media_scope[0]["id"]
    with media_jobs.connection(config) as db:
        if boundary == "other_owner":
            other = auth.create_client(db, "different-owner", False)
            auth.set_grant(db, other["id"], media_scope[1], add=True, query=True, original=True)
            cid = other["id"]
        elif boundary == "grant_revoked":
            auth.set_grant(db, cid, media_scope[1], add=False, query=False, original=False)
        elif boundary == "client_revoked":
            auth.revoke_client(db, cid)
        else:
            db.execute("UPDATE media_link_tasks SET state=? WHERE id=?", (boundary, identity))
    fake = binary(tmp_path, "import sys; sys.exit(0)")
    with (
        patch.dict(media_browser.BROWSERS, {"chrome": fake}),
        patch.object(media_browser.subprocess, "Popen") as launch,
    ):
        with pytest.raises((auth.Denied, InvalidInput)):
            media_browser.launch(config, cid, identity, "chrome")
        launch.assert_not_called()
    assert not (config.state_root / "browser-profiles").exists()


def test_blocked_task_can_request_handoff_but_not_replay_acquisition(config, media_scope, tmp_path):
    identity = task(config, media_scope)
    with media_jobs.connection(config) as db:
        db.execute("UPDATE media_link_tasks SET state='blocked' WHERE id=?", (identity,))
    fake = binary(tmp_path, "import sys; sys.exit(0)")
    with patch.dict(media_browser.BROWSERS, {"edge": fake}):
        assert (
            media_browser.launch(config, media_scope[0]["id"], identity, "edge")["state"]
            == "browser_handoff_pending"
        )
    with media_jobs.connection(config) as db:
        assert (
            db.execute("SELECT state FROM media_link_tasks WHERE id=?", (identity,)).fetchone()[0]
            == "blocked"
        )


def test_profile_symlink_never_spawns_or_touches_target(config, media_scope, tmp_path):
    identity = task(config, media_scope)
    base = config.state_root / "browser-profiles/chrome"
    base.mkdir(parents=True)
    target = tmp_path / "unrelated-profile"
    target.mkdir()
    (base / media_scope[0]["id"]).symlink_to(target, target_is_directory=True)
    fake = binary(tmp_path, "import sys; sys.exit(0)")
    with (
        patch.dict(media_browser.BROWSERS, {"chrome": fake}),
        patch.object(media_browser.subprocess, "Popen") as launch,
    ):
        with pytest.raises(InvalidInput, match="symlink"):
            media_browser.launch(config, media_scope[0]["id"], identity, "chrome")
        launch.assert_not_called()
    assert list(target.iterdir()) == []


def test_revocation_is_serialized_with_the_launch_attempt(config, media_scope, tmp_path):
    import threading

    identity = task(config, media_scope)
    fake = binary(tmp_path, "import time; time.sleep(30)")
    cid = media_scope[0]["id"]
    attempted = threading.Event()
    revoked = threading.Event()
    children = []
    real = subprocess.Popen

    def revoke():
        attempted.set()
        with media_jobs.connection(config) as db:
            auth.set_grant(db, cid, media_scope[1], add=False, query=False, original=False)
        revoked.set()

    worker = threading.Thread(target=revoke)

    def start(*args, **kwargs):
        process = real(*args, **kwargs)
        children.append(process)
        worker.start()
        assert attempted.wait(1)
        assert not revoked.wait(0.2)
        return process

    try:
        with (
            patch.dict(media_browser.BROWSERS, {"chrome": fake}),
            patch.object(media_browser.subprocess, "Popen", side_effect=start),
        ):
            assert (
                media_browser.launch(config, cid, identity, "chrome")["state"]
                == "browser_handoff_pending"
            )
        worker.join(timeout=5)
        assert revoked.is_set()
        with patch.object(media_browser.subprocess, "Popen") as launch:
            with pytest.raises(auth.Denied):
                media_browser.launch(config, cid, identity, "chrome")
            launch.assert_not_called()
    finally:
        worker.join(timeout=5)
        for child in children:
            child.terminate()
            child.wait(timeout=5)
