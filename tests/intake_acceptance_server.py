"""Opt-in automatic intake GUI fixture: synthetic originals, no SDK/network/model calls."""

import argparse
import json
import struct
import threading
import zlib
from pathlib import Path
from unittest.mock import patch

import uvicorn
from channel_acceptance_server import prepare
from test_articles import X_URL, current_html

from fiber_bundle import article_fetch, auth, database, feishu_receiver, weixin_batches
from fiber_bundle.api import create_app
from fiber_bundle.channels import (
    intake_contract,
    intake_extraction,
    intake_processing,
    intake_repository,
    store,
)
from fiber_bundle.http_guard import Settings
from fiber_bundle.weixin_events import Binding


def png_chunk(kind, data):
    return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))


# Decoder-valid synthetic 1px blue image, above the existing 128-byte minimum.
PNG = b"\x89PNG\r\n\x1a\n" + b"".join(
    (
        png_chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 2, 0, 0, 0)),
        png_chunk(b"tEXt", b"Comment\x00" + b"synthetic GUI fixture " * 8),
        png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\xff")),
        png_chunk(b"IEND", b""),
    )
)


def seed(config, root):
    manifest = json.loads((root / "fixture.json").read_text())
    identity = manifest["ids"]["feishu"]
    bound = Binding(
        "feishu:cli_synthetic", "ou_synthetic", manifest["client_id"], manifest["vault_id"]
    )
    with store.opened(config) as side:
        entry = store.get(side, identity)
        entry["value"].update(add=True, original=True)
        side.execute(
            "UPDATE channels SET value=? WHERE id=?", (json.dumps(entry["value"]), identity)
        )
    with database.connect(config.state_root) as main:
        auth.set_grant(main, bound.client_id, bound.vault_id, query=True, original=True, add=True)
    feishu_receiver.initialize(config, bound)

    def receive(event_id, text):
        from datetime import UTC, datetime, timedelta

        feishu_receiver.capture(
            config,
            bound,
            {
                "header": {"app_id": "cli_synthetic"},
                "event": {
                    "sender": {"sender_type": "user", "sender_id": {"open_id": bound.owner_id}},
                    "message": {
                        "message_id": event_id,
                        "chat_id": "oc_synthetic",
                        "chat_type": "p2p",
                        "message_type": "text",
                        "content": json.dumps({"text": text}),
                    },
                },
            },
        )
        feishu_receiver.advance(config, bound)
        return weixin_batches.advance(config, bound, now=datetime.now(UTC) + timedelta(seconds=5))[
            0
        ]["batch_id"]

    old = receive("synthetic-history", "历史合成消息，只有手动选择才处理。")
    policy = intake_repository.save(
        config,
        identity,
        intake_contract.Save(
            expected_revision=0,
            settings=intake_contract.Settings(
                enabled=True,
                target_vault_id=bound.vault_id,
                store_limit=20,
                download_limit=10,
                fetch_articles=True,
            ),
        ),
    )
    batch = receive("synthetic-article", X_URL)
    intake_processing.advance(config, identity)

    def fetch(url, limit, *, validate=None):
        if validate:
            validate(url)
        return article_fetch.Response(
            url,
            "text/html" if url == X_URL else "image/png",
            current_html() if url == X_URL else PNG,
            (url,),
        )

    with patch.object(article_fetch, "fetch_public", fetch):
        intake_extraction.advance(config, identity)
    held = receive("synthetic-not-allowed", "合成混合消息 https://unapproved.org/paper")
    intake_processing.advance(config, identity)
    intake_repository.save(
        config,
        identity,
        intake_contract.Save(
            expected_revision=policy["revision"],
            settings=intake_contract.Settings(
                enabled=False,
                target_vault_id=bound.vault_id,
                store_limit=20,
                download_limit=10,
                fetch_articles=False,
            ),
        ),
    )
    manifest.update(intake_batch=batch, history_batch=old, held_batch=held)
    (root / "fixture.json").write_text(json.dumps(manifest))
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18821)
    args = parser.parse_args()
    if not args.fixture.is_relative_to(Path("/private/tmp")) or not 18000 <= args.port <= 19000:
        parser.error("Use a fresh /private/tmp fixture and isolated port")
    config, children = prepare(args.fixture)
    identity = seed(config, args.fixture)
    stop = threading.Event()

    def archival_only_worker():
        while not stop.wait(0.2):
            intake_processing.advance(config, identity)

    thread = threading.Thread(target=archival_only_worker, daemon=True)
    thread.start()
    print(
        json.dumps(
            {"synthetic": True, "real_model": False, "platform_delivery": False, "port": args.port}
        ),
        flush=True,
    )
    try:
        uvicorn.run(
            create_app(config, Settings(port=args.port)),
            host="127.0.0.1",
            port=args.port,
            log_level="error",
            access_log=False,
        )
    finally:
        stop.set()
        thread.join(timeout=5)
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)


if __name__ == "__main__":
    main()
