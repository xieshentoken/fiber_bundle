"""Optional actual Hermes token-lock acceptance across isolated process homes.

The child is a scope-lock holder, not a full gateway. Its gateway/run.py filename
matches Hermes' process-identity check. Parent uses the complete candidate adapter.
All identities and homes are synthetic; no installed source/config is changed.
"""

import asyncio
import json
import logging
import os
import select
import subprocess
import sys
from pathlib import Path

HOLDER = """import os, sys
from pathlib import Path
def audit(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'os.system', 'subprocess.Popen'}:
        raise RuntimeError('external effects disabled in lock holder')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        if Path(os.fsdecode(args[0])).absolute().is_relative_to(Path.home() / '.hermes'):
            raise RuntimeError('production home reads disabled in lock holder')
sys.addaudithook(audit)
from gateway.status import acquire_scoped_lock, release_scoped_lock
assert acquire_scoped_lock('weixin-bot-token', 'synthetic-lock-token')[0]
print('ready', flush=True)
if sys.argv[1] == 'crash':
    os._exit(73)
sys.stdin.readline()
release_scoped_lock('weixin-bot-token', 'synthetic-lock-token')
"""


def main():
    root = Path(sys.argv[1]).resolve()
    home = root / ".state/hermes"
    lock_dir = root / ".state/platform-locks"
    assert os.environ["HERMES_HOME"] == str(home)
    assert os.environ["HERMES_GATEWAY_LOCK_DIR"] == str(lock_dir)
    root.mkdir(exist_ok=True)
    script = root / "holder/gateway/run.py"
    script.parent.mkdir(parents=True)
    script.write_text(HOLDER)
    holder_home = root / ".state/holder-home"
    holder_home.mkdir(parents=True)
    assert holder_home != home
    child_commands = [[sys.executable, str(script), mode] for mode in ("hold", "crash")]
    private_reads = []
    external_attempts = []
    children = []

    def audit(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "os.system"}:
            external_attempts.append(event)
            raise RuntimeError("network disabled in platform-lock acceptance")
        if event == "open" and isinstance(args[0], (str, bytes)):
            if Path(os.fsdecode(args[0])).absolute().is_relative_to(Path.home() / ".hermes"):
                private_reads.append("production_hermes_home")
                raise RuntimeError("production home reads disabled")
        if event == "subprocess.Popen":
            argv = args[1]
            checks = [["ps", "-p", str(child.pid), "-o", "command="] for child in children]
            if argv not in child_commands + checks:
                raise RuntimeError("only test holders and their identity checks allowed")

    sys.addaudithook(audit)
    logging.disable(logging.CRITICAL)
    from gateway.config import PlatformConfig
    from gateway.platforms import weixin
    from gateway.status import _get_scope_lock_path

    from fiber_bundle import auth, database
    from fiber_bundle import weixin_inbox as inbox
    from fiber_bundle.config import load_config
    from fiber_bundle.weixin_events import Binding
    from fiber_bundle.weixin_receiver import configure

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
    home.mkdir()
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic-owner", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vid, add=True, query=True, original=False)
    db.close()
    binding = Binding("synthetic-bot", "synthetic-owner", client["id"], vid)
    inbox.initialize(config, binding)
    pc = PlatformConfig(
        enabled=True,
        token="synthetic-lock-token",
        extra={
            "account_id": binding.account_id,
            "allow_from": [binding.owner_id],
            "dm_policy": "allowlist",
            "group_policy": "disabled",
        },
    )
    lock = _get_scope_lock_path("weixin-bot-token", "synthetic-lock-token")
    assert lock.parent == lock_dir

    def holder(mode):
        env = {**os.environ, "HERMES_HOME": str(holder_home)}
        process = subprocess.Popen(
            child_commands[mode == "crash"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        children.append(process)
        assert select.select([process.stdout], [], [], 10)[0], "holder startup timed out"
        assert process.stdout.readline().strip() == "ready"
        return process

    async def scenario():
        transport_calls = []

        async def updates(*args, **kwargs):
            transport_calls.append(True)
            await asyncio.Event().wait()

        weixin._get_updates = updates
        live = holder("hold")
        try:
            before = lock.read_bytes()
            assert json.loads(before)["hermes_home"] == str(holder_home)
            adapter = weixin.WeixinAdapter(pc)
            configure(adapter, config, binding)
            adapter._platform_lock_takeover_allowed = True
            assert not await adapter.connect()
            assert not adapter._platform_lock_takeover_allowed
            await adapter.disconnect()
            assert live.poll() is None and lock.read_bytes() == before
            assert not transport_calls and not weixin._LIVE_ADAPTERS
        finally:
            live.communicate("stop\n", timeout=10)
        assert live.returncode == 0 and not lock.exists()
        crashed = holder("crash")
        crashed.communicate(timeout=10)
        assert crashed.returncode == 73 and lock.exists()
        assert json.loads(lock.read_bytes())["pid"] == crashed.pid
        recovered = weixin.WeixinAdapter(pc)
        configure(recovered, config, binding)
        try:
            assert await recovered.connect()
            assert json.loads(lock.read_bytes())["pid"] == os.getpid()
            assert weixin._LIVE_ADAPTERS["synthetic-lock-token"] is recovered
        finally:
            await recovered.disconnect()
        assert not lock.exists() and not weixin._LIVE_ADAPTERS

    try:
        asyncio.run(scenario())
    finally:
        for child in children:
            if child.poll() is None:
                child.communicate("stop\n", timeout=10)
    assert not private_reads and not external_attempts
    print(
        json.dumps(
            {
                "cross_home_live_holder_preserved": "PASS",
                "takeover_disabled": "PASS",
                "foreign_lock_cleanup_preserved": "PASS",
                "dead_holder_recovery": "PASS",
                "real_candidate_adapter": "PASS",
                "network_calls": 0,
                "private_home_read_attempts": private_reads,
                "real_account_lock_and_feishu": "NOT_RUN",
            }
        )
    )


if __name__ == "__main__":
    main()
