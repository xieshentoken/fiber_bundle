"""Optional offline acceptance against the full pinned Hermes source snapshot.

Run in a fresh Python 3.12 process with sanitized environment, HERMES_HOME under
the isolated project state and HERMES_GATEWAY_LOCK_DIR under that same state.
PYTHONPATH must contain the prepared Hermes candidate and this project's src.
No production configuration, network, provider or external execution is allowed.
"""

import asyncio
import json
import logging
import os
import sys
import traceback
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    home = root / ".state/hermes"
    assert os.environ["HERMES_HOME"] == str(home)
    assert os.environ["HERMES_GATEWAY_LOCK_DIR"] == str(root / ".state/platform-locks")
    # Use the upstream explicit-path setting so import does not auto-download
    # Tirith. No tools execute in this capture-only acceptance; guards stay on.
    os.environ["TIRITH_BIN"] = str(home / "bin/tirith")
    private_reads = []
    network_attempts = []

    def audit(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
            network_attempts.append(
                {
                    "event": event,
                    "functions": [f.name for f in traceback.extract_stack(limit=30)[:-1]],
                }
            )
            raise RuntimeError("external effects disabled in offline gateway acceptance")
        if event == "open" and isinstance(args[0], (str, bytes)):
            path = Path(os.fsdecode(args[0])).absolute()
            if path.is_relative_to(Path.home() / ".hermes"):
                private_reads.append("production_hermes_home")
                raise RuntimeError("production home reads disabled")

    sys.addaudithook(audit)
    logging.disable(logging.CRITICAL)
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms import weixin
    from gateway.run import GatewayRunner
    from gateway.status import _get_scope_lock_path

    from fiber_bundle import auth, database
    from fiber_bundle import weixin_gateway as startup
    from fiber_bundle import weixin_inbox as inbox
    from fiber_bundle.config import load_config
    from fiber_bundle.weixin_events import Binding
    from fiber_bundle.weixin_receiver_config import ReceiverConfig

    root.mkdir(exist_ok=True)
    path = root / "config.json"
    path.write_text(
        json.dumps(
            {
                "state_root": ".state",
                "vaults": {
                    "personal": "test vault/temp_vault1",
                    "supplier": "test vault/temp_vault2",
                },
            }
        )
    )
    config = load_config(path, root)
    database.initialize(config)
    home.mkdir(exist_ok=True)
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic-gateway-owner", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vid, add=True, query=True, original=False)
    db.close()
    binding = Binding("synthetic-bot", "synthetic-owner", client["id"], vid)
    inbox.initialize(config, binding)

    async def scenario():
        pc = PlatformConfig(
            enabled=True,
            token="synthetic-token",
            gateway_restart_notification=False,
            extra={
                "account_id": binding.account_id,
                "base_url": "https://ilinkai.weixin.qq.com",
                "cdn_base_url": "https://novac2c.cdn.weixin.qq.com/c2c",
                "dm_policy": "allowlist",
                "allow_from": [binding.owner_id],
                "group_policy": "disabled",
            },
        )
        gc = GatewayConfig(
            platforms={Platform.WEIXIN: pc}, sessions_dir=home / "sessions", stt_enabled=False
        )
        runner = GatewayRunner(gc)
        settings = ReceiverConfig(binding, enabled=True, quiet_seconds=0.01)
        assert startup.install(config, settings, runner, weixin.WeixinAdapter, home=home)[
            "capture_only"
        ]
        fetched = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def get_updates(session, **kwargs):
            calls.append(kwargs["sync_buf"])
            if len(calls) == 1:
                fetched.set()
                return {
                    "ret": 0,
                    "get_updates_buf": "synthetic-one",
                    "msgs": [
                        {
                            "message_id": "gateway-one",
                            "from_user_id": binding.owner_id,
                            "to_user_id": binding.account_id,
                            "context_token": "synthetic-route-only",
                            "item_list": [{"type": 1, "text_item": {"text": "synthetic original"}}],
                        }
                    ],
                }
            await release.wait()
            runner.adapters[Platform.WEIXIN]._running = False
            return {"ret": 0, "get_updates_buf": "synthetic-two", "msgs": []}

        async def legacy(*args, **kwargs):
            raise AssertionError("legacy message or Agent dispatch must not run")

        weixin._get_updates = get_updates  # Only the iLink transport is a synthetic substitute.
        weixin.WeixinAdapter._process_message = legacy
        runner._handle_message = legacy
        lock = _get_scope_lock_path("weixin-bot-token", "synthetic-token")
        try:
            assert await runner.start()
            await asyncio.wait_for(fetched.wait(), timeout=10)
            for _ in range(200):
                if inbox.cursor(config, binding) == "synthetic-one":
                    break
                await asyncio.sleep(0.01)
            assert inbox.cursor(config, binding) == "synthetic-one"
            adapter = runner.adapters[Platform.WEIXIN]
            assert adapter._fiber_inbox == (config, binding) and lock.exists()
            # Hermes' token lock is PID-based. A duplicate object in this process must
            # fail before lock acquisition, and its cleanup must preserve the live owner.
            second = weixin.WeixinAdapter(pc)
            from fiber_bundle.weixin_receiver import configure

            configure(second, config, binding)
            assert not await second.connect()
            await second.disconnect()
            assert weixin._LIVE_ADAPTERS["synthetic-token"] is adapter and lock.exists()
            assert len(calls) <= 2 and adapter._running
            release.set()
            for _ in range(200):
                if inbox.cursor(config, binding) == "synthetic-two":
                    break
                await asyncio.sleep(0.01)
            assert inbox.cursor(config, binding) == "synthetic-two"
        finally:
            release.set()
            await runner.stop()
        assert not runner._running and not lock.exists()
        assert not weixin._LIVE_ADAPTERS
        with inbox.connection(config, binding) as journal:
            assert journal.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        db = database.connect(config.state_root)
        counts = {
            table: db.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in ("receipts", "execution_runs", "model_calls", "article_publications")
        }
        db.close()
        assert not any(counts.values())
        return {
            "full_gateway_import": "PASS",
            "full_runner_start_stop": "PASS",
            "real_adapter_connect_disconnect": "PASS",
            "capture_only_journal": "PASS",
            "duplicate_adapter_cleanup": "PASS",
            "counts": counts,
            "private_home_read_attempts": private_reads,
            "blocked_external_effect_attempts": network_attempts,
            "real_phone_provider_docker_feishu": "NOT_RUN",
        }

    result = asyncio.run(scenario())
    assert not private_reads and not network_attempts
    print(json.dumps(result))


if __name__ == "__main__":
    main()
