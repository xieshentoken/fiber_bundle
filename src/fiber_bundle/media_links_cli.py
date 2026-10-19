"""Explicit public-link acquisition or authorized browser copy; no cookies/secret arguments."""

import argparse
import json
import sqlite3
from pathlib import Path

from . import auth, media_browser, media_jobs, media_links
from .client_token import read as token
from .config import StorageError, load_config


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "command",
        choices=[
            "preflight",
            "create",
            "run",
            "status",
            "cancel",
            "recover",
            "browser",
            "import-browser",
        ],
    )
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--config", type=Path, default=Path("config.test.json"))
    p.add_argument("--token-file", type=Path, required=True)
    p.add_argument("--link-id")
    p.add_argument("--vault-id")
    p.add_argument("--url")
    p.add_argument("--mode", choices=["audio_only", "full_evidence"], default="audio_only")
    p.add_argument("--duration-ms", type=int)
    p.add_argument("--image")
    p.add_argument("--media-image")
    p.add_argument("--keep-media", action="store_true")
    p.add_argument("--browser", choices=["chrome", "edge"], default="chrome")
    p.add_argument("--media-file", type=Path)
    p.add_argument("--authorized-playback", action="store_true")
    a = p.parse_args()
    try:
        root = a.project_root.resolve(strict=True)
        config = load_config(a.config if a.config.is_absolute() else root / a.config, root)
        with media_jobs.connection(config) as db:
            cid = auth.client(db, "Bearer " + token(a.token_file))["id"]
        if a.command == "preflight":
            if any(v is None for v in (a.vault_id, a.url, a.image)):
                p.error("preflight requires vault, URL and image")
            from .media_link_preflight import run as preflight

            value = preflight(config, cid, a.vault_id, a.url, a.mode, a.image)
        elif a.command == "create":
            if any(v is None for v in (a.vault_id, a.url, a.duration_ms, a.image)):
                p.error("create requires vault, URL, duration and image")
            value = {
                "link_id": media_links.create(
                    config,
                    cid,
                    a.vault_id,
                    a.url,
                    a.mode,
                    a.duration_ms,
                    a.image,
                    keep_media=a.keep_media,
                )
            }
        else:
            if a.link_id is None:
                p.error("command requires --link-id")
            if a.command == "run":
                if a.media_image is None:
                    p.error("run requires --media-image")
                value = media_links.run(config, cid, a.link_id, a.media_image)
            elif a.command == "browser":
                value = media_browser.launch(config, cid, a.link_id, a.browser)
            elif a.command == "import-browser":
                if a.media_file is None or a.media_image is None:
                    p.error("import-browser requires media file and image")
                # Preserve the supplied browser copy; no directory or credential import.
                from .media_import_file import read as import_file

                value = media_links.browser_import(
                    config,
                    cid,
                    a.link_id,
                    import_file(a.media_file),
                    a.media_image,
                    authorized_playback=a.authorized_playback,
                )
            elif a.command in ("cancel", "recover"):
                value = media_links.stop(config, cid, a.link_id, recover=a.command == "recover")
            else:
                value = media_links.status(config, cid, a.link_id)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        print(json.dumps({"error": "media_link_request_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
