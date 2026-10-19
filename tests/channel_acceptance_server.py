"""Opt-in channel GUI fixture: production APIs, synthetic leases, no SDK or provider calls.

Each active GUI entry has an owned child process that exits after permission withdrawal.
The legacy entry exposes transport evidence only. Never point this at project state.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

from fiber_bundle import auth, database, feishu_runtime
from fiber_bundle.api import create_app
from fiber_bundle.channels import configuration, legacy, store
from fiber_bundle.config import Config
from fiber_bundle.http_guard import Settings


def pulse(home: Path, database_path: Path, identity: str, platform: str, state: str) -> None:
    with feishu_runtime.Instance(home, platform=platform) as instance:
        while True:
            with sqlite3.connect(database_path) as db:
                current = db.execute(
                    "SELECT state FROM channels WHERE id=?", (identity,)
                ).fetchone()
            if current[0] != state:
                break
            transport = "connected" if state == "running" else "waiting_for_login"
            instance.status(transport, {})
            feishu_runtime.write(
                home / "channel-status.json",
                {
                    "instance_id": instance.identity,
                    "revision": 1,
                    "nonce": "synthetic-gui-fixture",
                    "state": "query_ready" if state == "running" else "waiting_for_login",
                    "platform": platform,
                },
            )
            time.sleep(0.1)


def prepare(root: Path) -> tuple[Config, list[subprocess.Popen]]:
    root.mkdir(mode=0o700)
    config = Config(
        root,
        ".state",
        (("personal", "test vault/temp_vault1"), ("supplier", "test vault/temp_vault2")),
    )
    database.initialize(config)
    auth.set_password(config, "synthetic-gui-acceptance")
    note = root / "test vault/temp_vault1/a.md"
    note.write_text("# 合成通道资料\n合成压力记录 2.4 MPa。\n")
    main = database.connect(config.state_root)
    client = auth.create_client(main, "通道验收合成身份", False)
    vid = main.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    manifest = root / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source": "unused",
                "dependencies": [],
                "sha256": {},
                "scanner": "unused",
                "scanner_sha256": "a" * 64,
            }
        )
    )
    values = {}
    children = []
    for key, name, platform, state in (
        ("feishu", "飞书 · 资料与查询", "feishu", "running"),
        ("weixin", "微信 · 本人入口", "weixin", "running"),
        ("login", "微信 · 等待扫码", "weixin", "login"),
        ("error", "飞书 · 异常示例", "feishu", "error"),
    ):
        entry = configuration.save(
            config,
            main,
            configuration.Save(
                settings=configuration.Settings(
                    name=name,
                    platform=platform,
                    app_id="cli_synthetic" if platform == "feishu" else None,
                    client_id=client["id"],
                    vault_id=vid,
                    runtime_manifest=str(manifest),
                    query_limit=4,
                    outbound_limit=4,
                ),
                app_secret="SYNTHETIC_CHANNEL_CREDENTIAL_0000" if platform == "feishu" else None,
            ),
        )
        values[key] = entry["id"]
        home = store.home(config, entry["id"])
        with store.opened(config) as side:
            side.execute(
                "UPDATE channels SET state=?,owner=? WHERE id=?",
                (state, None if state == "login" else "ou_synthetic", entry["id"]),
            )
            if key == "feishu":
                side.execute(
                    "INSERT INTO events VALUES(?,?,1,'sent','{}',?)",
                    (entry["id"], "synthetic-spent", time.time()),
                )
        if key == "weixin":
            (home / "weixin/accounts").mkdir(parents=True)
            (home / "weixin/accounts/synthetic.json").write_text('{"token":"SYNTHETIC_TOKEN"}')
            (home / "weixin-login.json").write_text('{"owner_id":"ou_synthetic"}')
        if state in {"running", "login"}:
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        __file__,
                        "--pulse",
                        str(home),
                        str(config.state_root / "message-channels.sqlite3"),
                        entry["id"],
                        platform,
                        state,
                    ],
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
            )
    old = config.state_root / "configs/hermes-feishu-existing"
    old.mkdir(parents=True)
    (old / "receiver.json").write_text(
        json.dumps(
            {
                "version": 1,
                "enabled": True,
                "app_id": "cli_legacy",
                "owner_id": "ou_synthetic",
                "home": "configs/hermes-feishu-existing",
                "client_id": client["id"],
                "vault_id": vid,
                "download_limit": 0,
                "store_limit": 0,
                "model_limit": 0,
                "outbound_limit": 0,
            }
        )
    )
    values["legacy"] = legacy.entry(config, old / "receiver.json")["id"]
    # Keep this process leased; the browser tests never control the legacy fixture.
    old_instance = feishu_runtime.Instance(old)
    old_instance.__enter__()
    import threading

    def legacy_pulse():
        while True:
            old_instance.status("connected", {})
            time.sleep(0.5)

    threading.Thread(target=legacy_pulse, daemon=True).start()
    main.close()
    (root / "fixture.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "server_pid": os.getpid(),
                "ids": values,
                "vault_id": vid,
                "client_id": client["id"],
            }
        )
        + "\n"
    )
    return config, children


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--pulse":
        pulse(Path(sys.argv[2]), Path(sys.argv[3]), *sys.argv[4:7])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18789)
    args = parser.parse_args()
    if not 18000 <= args.port <= 19000 or not args.fixture.is_relative_to(Path("/private/tmp")):
        parser.error("Use a new /private/tmp fixture and an isolated port 18000..19000")
    config, children = prepare(args.fixture)
    print(json.dumps({"synthetic": True, "real_model": False, "port": args.port}), flush=True)
    try:
        uvicorn.run(
            create_app(config, Settings(port=args.port)),
            host="127.0.0.1",
            port=args.port,
            log_level="error",
            access_log=False,
        )
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)


if __name__ == "__main__":
    main()
