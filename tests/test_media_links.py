import hashlib
import socket
from unittest.mock import patch

import pytest
from test_media_jobs import media_scope as media_scope

from fiber_bundle import (
    auth,
    media_browser,
    media_jobs,
    media_link_capture,
    media_link_worker,
    media_links,
)
from fiber_bundle.config import InvalidInput
from fiber_bundle.media_link_contract import source_url


@pytest.mark.parametrize(
    "url",
    [
        "http://youtu.be/BaW_jenozKc",
        "https://user:secret@youtube.com/watch?v=BaW_jenozKc",
        "https://youtube.com/watch?v=BaW_jenozKc&list=playlist",
        "https://127.0.0.1/test",
        "https://www.youtube.com:443/watch?v=BaW_jenozKc",
        "https://youtube.com/watch?bad",
        "https://www.bilibili.com/video/BV1xx411c7mD?token=secret",
        "https://youtu.be/BaW_jenozKc#t=3",
    ],
)
def test_unsupported_urls_or_session_parameters_are_refused(url):
    with pytest.raises(InvalidInput):
        source_url(url)


def test_url_matrix_is_canonical():
    assert (
        source_url("https://youtu.be/BaW_jenozKc") == "https://www.youtube.com/watch?v=BaW_jenozKc"
    )
    assert (
        source_url("https://bilibili.com/video/BV1xx411c7mD/")
        == "https://www.bilibili.com/video/BV1xx411c7mD"
    )


def task(config, scope):
    return media_links.create(
        config,
        scope[0]["id"],
        scope[1],
        "https://youtu.be/BaW_jenozKc",
        "audio_only",
        3000,
        "sha256:" + "a" * 64,
    )


def test_explicit_browser_import_is_bound_and_cannot_replay(config, media_scope):
    identity = task(config, media_scope)
    cid = media_scope[0]["id"]
    data = media_scope[2][0][2]
    with pytest.raises(InvalidInput):
        media_links.browser_import(
            config, cid, identity, data, "sha256:" + "b" * 64, authorized_playback=False
        )
    result = media_links.browser_import(
        config, cid, identity, data, "sha256:" + "b" * 64, authorized_playback=True
    )
    assert result["state"] == "complete" and not result["publication_ready"]
    with media_jobs.connection(config) as db:
        job = media_jobs.load(db, cid, result["job_id"])
        assert media_jobs.binding(job)["source"]["sha256"] == hashlib.sha256(data).hexdigest()
    with pytest.raises(InvalidInput):
        media_links.browser_import(
            config, cid, identity, data, "sha256:" + "b" * 64, authorized_playback=True
        )
    assert media_scope[2][0][2] == data


def test_public_refusal_pauses_without_cookie_fallback_or_replay(config, media_scope):
    identity = task(config, media_scope)
    cid = media_scope[0]["id"]
    with patch.object(
        media_link_worker, "execute", side_effect=InvalidInput("synthetic login refused")
    ) as worker:
        with pytest.raises(InvalidInput):
            media_links.run(config, cid, identity, "sha256:" + "b" * 64)
        with pytest.raises(InvalidInput, match="no_replay"):
            media_links.run(config, cid, identity, "sha256:" + "b" * 64)
        assert worker.call_count == 1
    assert media_links.status(config, cid, identity)["browser_handoff_available"]


@pytest.mark.parametrize("change", ["cancel", "revoke"])
def test_public_attempt_stops_when_owner_boundary_changes(config, media_scope, change):
    identity = task(config, media_scope)
    cid = media_scope[0]["id"]

    def execute(config, identity, image, spec, *, guard):
        if change == "cancel":
            media_links.stop(config, cid, identity)
        else:
            with media_jobs.connection(config) as db:
                auth.set_grant(db, cid, media_scope[1], add=False, query=False, original=False)
        guard()
        raise AssertionError("stopped attempt cannot import")

    with patch.object(media_link_worker, "execute", side_effect=execute):
        with pytest.raises((InvalidInput, auth.Denied)):
            media_links.run(config, cid, identity, "sha256:" + "b" * 64)
    with media_jobs.connection(config) as db:
        assert db.execute("SELECT count(*) FROM media_tasks").fetchone()[0] == 0


def test_dedicated_browser_uses_only_task_client_profile(config, media_scope, tmp_path):
    identity = task(config, media_scope)
    binary = tmp_path / "browser"
    binary.write_text("synthetic")
    with (
        patch.dict(media_browser.BROWSERS, {"chrome": str(binary)}),
        patch("fiber_bundle.media_browser.subprocess.Popen") as launch,
    ):
        launch.return_value.wait.return_value = 0
        result = media_browser.launch(config, media_scope[0]["id"], identity, "chrome")
    args = launch.call_args.args[0]
    assert (
        "--user-data-dir="
        + str(config.state_root / "browser-profiles/chrome" / media_scope[0]["id"])
        in args
    )
    assert not result["cookies_exported"] and not result["login_verified"]
    with media_jobs.connection(config) as db:
        stranger = auth.create_client(db, "other-client", False)
        auth.set_grant(db, stranger["id"], media_scope[1], add=True, query=True, original=True)
    with pytest.raises(auth.Denied):
        media_browser.launch(config, stranger["id"], identity, "chrome")


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_python_network_boundary_refuses_private_addresses(ip):
    # Restore global socket methods after exercising the container-only boundary.
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    with (
        patch.object(
            socket, "getaddrinfo", return_value=[(family, socket.SOCK_STREAM, 6, "", (ip, 443))]
        ),
        patch.object(socket.socket, "connect"),
        patch.object(socket.socket, "connect_ex"),
    ):
        media_link_capture.public_sockets()
        with pytest.raises(OSError):
            socket.getaddrinfo("example.test", 443)
        with pytest.raises(OSError):
            socket.getaddrinfo("example.test", 80)


def test_progress_bound_rejects_over_budget_download():
    with pytest.raises(InvalidInput):
        media_link_capture.bounded_progress({"downloaded_bytes": 8 * 1024 * 1024 + 1})
    media_link_capture.bounded_progress({"downloaded_bytes": 1024, "total_bytes": None})
