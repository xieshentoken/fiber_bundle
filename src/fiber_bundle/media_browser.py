"""Dedicated browser launch/handoff; login remains inside its separate profile."""

import re
import subprocess
from pathlib import Path
from typing import Any

from . import media_jobs, media_links
from .config import Config, InvalidInput, contained, make_directory

BROWSERS = {
    "chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "edge": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
}


def launch(config: Config, cid: str, identity: str, browser: str) -> dict[str, Any]:
    if browser not in BROWSERS:
        raise InvalidInput("media_browser_backend")
    with media_jobs.connection(config) as db:
        row = media_links.access(config, db, cid, identity)
        if row["state"] not in ("queued", "blocked"):
            raise InvalidInput("media_browser_task_stopped")
        # Separate profile for each authorized application client; never use/copy default profiles.
        if not re.fullmatch("[a-f0-9-]{36}", cid):
            raise InvalidInput("media_browser_client_scope")
        binary = Path(BROWSERS[browser])
        if not binary.is_file():
            raise InvalidInput("media_browser_unavailable")
        profile = contained(config.state_root, "browser-profiles/" + browser + "/" + cid)
        make_directory(profile)
        # Keep live authorization serialized until the launch attempt completes.
        process = subprocess.Popen(
            [
                str(binary),
                "--user-data-dir=" + str(profile),
                "--no-first-run",
                "--no-default-browser-check",
                row["url"],
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            code = process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass  # Still running; a native window/login has not been verified.
        else:
            if code != 0:
                raise InvalidInput("media_browser_launch_failed")
            # Zero may be forwarding to an already running, same-profile browser.
    return {
        "link_id": identity,
        "state": "browser_handoff_pending",
        "backend": browser,
        "login_verified": False,
        "cookies_exported": False,
        "publication_ready": False,
    }
