"""One GUI-authorized private process; no automatic restart, Agent dispatch or model fallback."""

import argparse
import asyncio
import contextlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .. import (
    database,
    feishu_budgets,
    feishu_gateway_cli,
    feishu_receiver,
    feishu_runtime,
    query_policy_store,
)
from ..config import Config, InvalidInput, load_config
from ..query_routing_files import read
from ..weixin_events import Binding
from . import configuration, dispatch, feishu, intake_repository, store, weixin


def owns_launch(home: Path, launch: dict[str, Any]) -> bool:
    try:
        latest = json.loads(read(home / "launch.json", 65536))
        return isinstance(latest, dict) and all(
            latest.get(key) == launch.get(key) and key in launch
            for key in ("channel_id", "revision", "action", "nonce")
        )
    except (OSError, ValueError, InvalidInput):
        return False


async def run(config: Config, launch: dict[str, Any]) -> None:
    identity, revision, action = launch["channel_id"], launch["revision"], launch["action"]
    home = store.home(config, identity)
    with store.opened(config) as db:
        channel = store.get(db, identity)
        if channel["revision"] != revision or channel["state"] != (
            "running" if action == "run" else action
        ):
            raise InvalidInput("channel_launch_withdrawn")
    state = "starting"
    module = feishu if channel["value"]["platform"] == "feishu" else weixin
    adapter = None
    succeeded = False
    budgets: dict[str, int] = {}
    from .receiver_lease import owned

    with (
        owned(config, channel, action),
        feishu_runtime.Instance(home, platform=channel["value"]["platform"]) as instance,
    ):
        # A delayed launch must recheck withdrawal after acquiring the original home lease.
        with database.locked(config), store.opened(config) as db:
            current = store.get(db, identity)
            if (
                current["revision"] != revision
                or current["state"] != ("running" if action == "run" else action)
                or not owns_launch(home, launch)
            ):
                raise InvalidInput("channel_launch_withdrawn")
            channel = current
        instance.status("starting", {})
        feishu_gateway_cli.isolate(False)
        feishu_gateway_cli.bootstrap(
            config, json.loads(read(Path(channel["value"]["runtime_manifest"]), 65536)), home
        )

        async def pulse() -> None:
            while True:
                connected = state == "query_ready" and adapter is not None and adapter.is_connected
                reconnecting = (
                    connected and getattr(adapter, "_fiber_poll_state", None) == "reconnecting"
                )
                observed = "reconnecting" if reconnecting else state
                instance.status(
                    "reconnecting"
                    if reconnecting
                    else "connected"
                    if connected
                    else "disconnected"
                    if state == "query_ready"
                    else state,
                    budgets,
                )
                feishu_runtime.write(
                    home / "channel-status.json",
                    {
                        "instance_id": instance.identity,
                        "state": observed,
                        "platform": channel["value"]["platform"],
                        "revision": revision,
                        "nonce": launch["nonce"],
                        "runtime_version": launch.get("runtime_image"),
                    },
                )
                await asyncio.sleep(1)

        async def withdrawn() -> None:
            while True:
                with store.opened(config) as db:
                    current = store.get(db, identity)
                if current["revision"] != revision or current["state"] != (
                    "running" if action == "run" else action
                ):
                    raise InvalidInput("channel_operation_withdrawn")
                await asyncio.sleep(1)

        pulse_task = asyncio.create_task(pulse())
        withdraw_task = asyncio.create_task(withdrawn())
        work = None
        intake_task = None
        try:
            if action == "check":
                state = "checking"
                if module is feishu:
                    result = await asyncio.to_thread(feishu.validate_credentials, config, channel)
                else:
                    weixin.upstream()
                    result = {"check": "login_adapter_verified", "network": "not_started"}
                feishu_runtime.write(
                    home / "check-result.json", {**result, "checked_at": time.time()}
                )
                succeeded = True
            elif action in {"pair", "login"}:
                state = "waiting_for_pairing" if action == "pair" else "waiting_for_login"
                (home / "pairing-candidate.json").unlink(missing_ok=True)
                work = asyncio.create_task(
                    feishu.pair(config, channel)
                    if action == "pair"
                    else weixin.login(config, channel)
                )
                done, _ = await asyncio.wait(
                    {work, withdraw_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if withdraw_task in done:
                    await withdraw_task
                candidate = work.result()
                candidate["expires_at"] = time.time() + 600
                with database.locked(config), store.opened(config) as db:
                    current = store.get(db, identity)
                    if current["revision"] != revision or current["state"] != action:
                        raise InvalidInput("channel_pairing_withdrawn")
                    db.execute(
                        "UPDATE channels SET candidate=?,state='unpaired' WHERE id=?",
                        (json.dumps(candidate), identity),
                    )
                state = "candidate_received"
                succeeded = True
            else:
                from .services.control import ensure_new

                await asyncio.to_thread(ensure_new, config, identity)
                adapter = module.adapter(config, channel)
                if channel["value"]["add"] and module is feishu:
                    await asyncio.to_thread(
                        feishu_receiver.initialize,
                        config,
                        configuration.feishu_settings(config, channel).binding,
                    )
                if channel["value"]["add"] and module is weixin:
                    from .. import weixin_confirmations, weixin_inbox

                    binding = Binding(
                        adapter._account_id,
                        channel["owner"],
                        channel["value"]["client_id"],
                        channel["value"]["vault_id"],
                    )
                    await asyncio.to_thread(weixin_inbox.initialize, config, binding)
                    await asyncio.to_thread(weixin_confirmations.initialize, config, binding)
                if not await asyncio.wait_for(adapter.connect(), 45):
                    raise InvalidInput("channel_connection_failed")
                state = "query_ready"
                from uuid import NAMESPACE_URL, uuid5

                grant = str(uuid5(NAMESPACE_URL, "fiber-channel:" + identity + ":" + str(revision)))
                if channel["value"]["add"] and module is feishu:
                    binding = configuration.feishu_settings(config, channel).binding
                    await asyncio.to_thread(
                        feishu_budgets.authorize,
                        config,
                        binding,
                        grant,
                        channel["value"]["download_limit"],
                        0,
                        0,
                        channel["value"].get("expert_receipt_limit", 0),
                    )
                if channel["value"]["add"] and module is feishu:
                    from .intake_background import serve

                    intake_task = asyncio.create_task(serve(config, identity, adapter))
                while True:
                    if withdraw_task.done():
                        await withdraw_task
                    if adapter._fiber_error:
                        cause = getattr(adapter, "_fiber_exception", None)
                        if isinstance(cause, Exception):
                            raise cause
                        raise InvalidInput("channel_capture_failed")
                    if not adapter.is_connected:
                        raise InvalidInput("channel_connection_stopped")
                    from ..vault_relocation.guard import paused

                    if "query_vaults" not in channel["value"] and await asyncio.to_thread(
                        paused, config, channel["value"]["vault_id"]
                    ):
                        await asyncio.sleep(0.5)
                        continue
                    from ..vault_relocation.errors import RelocationError

                    try:
                        await asyncio.to_thread(dispatch.advance, config, identity, revision)

                        async def sender(owner: str, body: str, context: str, guard: Any) -> bool:
                            return bool(await module.send(adapter, owner, body, context, guard))

                        if await dispatch.deliver(config, identity, revision, sender) == "unknown":
                            raise InvalidInput("channel_delivery_requires_reconciliation")
                        if channel["value"]["add"] and module is feishu:
                            binding = configuration.feishu_settings(config, channel).binding
                            await asyncio.to_thread(feishu_receiver.advance, config, binding)
                            from ..intake_abort_commands import deliver as deliver_abort

                            async def abort_sender(owner: str, body: str, checked: Any) -> bool:
                                return bool(await module.send(adapter, owner, body, "", checked))

                            await deliver_abort(
                                config,
                                binding,
                                abort_sender,
                                lambda: dispatch.archival_guard(config, identity, revision),
                            )
                            from ..organization.commands import deliver as deliver_organization

                            await deliver_organization(
                                config,
                                binding,
                                abort_sender,
                                lambda: dispatch.archival_guard(config, identity, revision),
                            )
                            from .expert import tick as expert_tick

                            await expert_tick(config, channel, adapter)
                            _, intake = await asyncio.to_thread(
                                intake_repository.channel, config, identity
                            )
                            if channel["value"]["download_limit"] and intake["revision"] == 0:

                                def reserve(
                                    db: sqlite3.Connection, target: str, bound: Binding = binding
                                ) -> bool:
                                    dispatch.archival_guard(config, identity, revision)
                                    return feishu_budgets.reserve_locked(
                                        db, bound, grant, "download", target
                                    )

                                await feishu_receiver.download_one(
                                    config, binding, adapter, reserve=reserve
                                )
                        if channel["value"]["add"] and module is feishu:
                            budgets = await asyncio.to_thread(
                                feishu_budgets.remaining, config, binding, grant
                            )
                    except RelocationError:
                        await asyncio.sleep(0.5)
                        continue
                    await asyncio.sleep(1)
        finally:
            for task in (pulse_task, withdraw_task, work, intake_task):
                if task:
                    task.cancel()
            await asyncio.gather(
                *(t for t in (pulse_task, withdraw_task, work, intake_task) if t),
                return_exceptions=True,
            )
            if adapter is not None:
                await asyncio.wait_for(adapter.disconnect(), 20)
            with database.locked(config), store.opened(config) as db:
                current = store.get(db, identity)
                if (
                    owns_launch(home, launch)
                    and current["revision"] == revision
                    and current["state"]
                    in {
                        "running",
                        "check",
                        "pair",
                        "login",
                    }
                ):
                    store.set_state(
                        db,
                        identity,
                        ("paused" if current["owner"] else "unpaired") if succeeded else "error",
                    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    launch = json.loads(read(args.configuration, 65536))
    if launch["nonce"] != args.nonce:
        return 1
    storage_path = args.configuration.parent / ("storage-" + args.nonce + ".json")
    query_policy_store.write_private(storage_path, json.dumps(launch["storage"]).encode())
    config = load_config(storage_path, args.project_root)
    try:
        with (
            contextlib.redirect_stdout(feishu_gateway_cli.SilentOutput()),
            contextlib.redirect_stderr(feishu_gateway_cli.SilentOutput()),
        ):
            asyncio.run(run(config, launch))
        return 0
    except Exception as exc:
        with database.locked(config), store.opened(config) as db:
            channel = store.get(db, launch["channel_id"])
            failed = (
                owns_launch(args.configuration.parent, launch)
                and channel["revision"] == launch["revision"]
                and channel["state"]
                in {
                    "running",
                    "check",
                    "pair",
                    "login",
                    "error",
                }
            )
            if failed:
                store.set_state(db, channel["id"], "error")
            if failed:
                from .diagnostics import failure

                diagnostic = failure(exc)
                feishu_runtime.write(
                    args.configuration.parent / "check-result.json",
                    {"check": "failed", **diagnostic, "checked_at": time.time()},
                )
        return 1
    finally:
        storage_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
