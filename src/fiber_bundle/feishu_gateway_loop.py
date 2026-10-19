"""Receiver scheduling; heartbeat and stop remain responsive during bounded storage work."""

import asyncio
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from . import (
    feishu_budgets,
    feishu_delivery,
    feishu_queries,
    feishu_receiver,
    feishu_runtime,
    feishu_sync,
    weixin_result_queue,
    weixin_store_dispatch,
)
from .config import Config, InvalidInput
from .execution_config import load_execution_config
from .feishu_gateway import Settings


async def run(
    config: Config, settings: Settings, home: Path, runner: Any, platform: Any, stop: asyncio.Event
) -> dict[str, Any]:
    from .channels import intake_repository, intake_runtime

    intake_id = intake_repository.legacy_id(config, settings.home)
    budgets = {"download": 0, "store": 0, "model": 0, "outbound": 0}
    limits = {"model": settings.model_limit, "outbound": settings.outbound_limit}
    worker: subprocess.Popen[bytes] | None = None
    policy = (
        load_execution_config(Path(settings.execution_file))
        if settings.query_enabled and settings.execution_file
        else None
    )
    state, processing = "starting", "healthy"
    with feishu_runtime.Instance(home, settings.binding.account_id) as instance:

        async def heartbeat() -> None:
            while True:
                instance.status(state, budgets, processing=processing, limits=limits)
                await asyncio.sleep(1)

        pulse = asyncio.create_task(heartbeat())
        intake_task = None
        try:
            await feishu_sync.run(feishu_receiver.initialize, config, settings.binding)
            await feishu_sync.run(
                feishu_budgets.authorize,
                config,
                settings.binding,
                settings.authorization_id,
                settings.download_limit,
                settings.store_limit,
                settings.model_limit,
                settings.outbound_limit,
            )
            budgets = await feishu_sync.run(
                feishu_budgets.remaining, config, settings.binding, settings.authorization_id
            )
            if not await asyncio.wait_for(runner.start(), timeout=45):
                raise InvalidInput("Feishu gateway startup failed")
            adapter = runner.adapters.get(platform)
            if adapter is None:
                raise InvalidInput("Feishu archival adapter unavailable")

            def reserve(kind: feishu_budgets.Kind, db: sqlite3.Connection, target: str) -> bool:
                return feishu_budgets.reserve_locked(
                    db, settings.binding, settings.authorization_id, kind, target
                )

            state = "connected"
            if intake_id is not None:
                from .channels.intake_background import serve

                intake_task = asyncio.create_task(serve(config, intake_id, adapter))
            while not stop.is_set():
                from .channels.legacy_control import guard

                guard(config, settings.home)
                if adapter._fiber_error:
                    raise InvalidInput("Feishu event capture failed")
                state = "connected" if adapter.is_connected else "disconnected"
                try:
                    await feishu_sync.run(
                        feishu_receiver.advance,
                        config,
                        settings.binding,
                        settings.quiet_seconds,
                        intent_routing=settings.query_enabled,
                    )
                    if stop.is_set():
                        break
                    from .intake_abort_commands import deliver as deliver_abort

                    async def abort_sender(owner: str, body: str, checked: Any) -> bool:
                        result = await feishu_sync.run(
                            adapter._fiber_send_receipt, owner, body, checked
                        )
                        return bool(result.get("category") == "confirmed")

                    await deliver_abort(
                        config, settings.binding, abort_sender, lambda: guard(config, settings.home)
                    )
                    from .organization.commands import deliver as deliver_organization

                    await deliver_organization(
                        config, settings.binding, abort_sender, lambda: guard(config, settings.home)
                    )
                    if settings.expert_enabled and settings.authorization_id:
                        from .expert_feishu import dispatch_one

                        await feishu_sync.run(
                            dispatch_one,
                            config,
                            settings.binding,
                            settings.authorization_id,
                            guard=lambda: guard(config, settings.home),
                        )
                    if (
                        settings.expert_enabled
                        and settings.authorization_id
                        and settings.outbound_limit
                    ):
                        from .expert_intent_receipts import deliver as deliver_expert_receipt

                        async def expert_sender(owner: str, message: str, checked: Any) -> bool:
                            result = await feishu_sync.run(
                                adapter._fiber_send_receipt, owner, message, checked
                            )
                            return bool(result.get("category") == "confirmed")

                        await deliver_expert_receipt(
                            config,
                            settings.binding,
                            settings.authorization_id,
                            expert_sender,
                            lambda: guard(config, settings.home),
                        )
                    intake_configured = (
                        intake_id is not None
                        and (await feishu_sync.run(intake_repository.channel, config, intake_id))[
                            1
                        ]["revision"]
                        > 0
                    )
                    if budgets["download"] and not intake_configured:
                        await feishu_receiver.download_one(
                            config,
                            settings.binding,
                            adapter,
                            reserve=lambda db, target: reserve("download", db, target),
                        )
                    if stop.is_set():
                        break
                    if budgets["store"]:
                        await feishu_sync.run(
                            weixin_store_dispatch.drain,
                            config,
                            settings.binding,
                            maximum=1,
                            reserve=lambda db, target: reserve("store", db, target),
                            eligible=lambda batch: (
                                not intake_runtime.owns_batch(config, intake_id, batch)
                            ),
                        )
                    if worker is not None and worker.poll() is not None:
                        worker.wait()
                        worker = None
                    if policy is not None and budgets["model"]:
                        await feishu_sync.run(
                            feishu_queries.submit_ready,
                            config,
                            settings.binding,
                            policy,
                            settings.authorization_id,
                        )
                        if worker is None and await feishu_sync.run(
                            feishu_queries.ready,
                            config,
                            settings.binding,
                            policy,
                            settings.authorization_id,
                        ):
                            worker = await feishu_sync.run(
                                feishu_queries.launch,
                                config,
                                settings.binding,
                                policy,
                                settings.authorization_id,
                                home,
                            )
                    if policy is not None and budgets["outbound"]:
                        batch = await feishu_sync.run(
                            weixin_result_queue.next_batch, config, settings.binding
                        )
                        if batch is not None:
                            outcome = await feishu_delivery.deliver(
                                config,
                                settings.binding,
                                batch,
                                settings.authorization_id,
                                adapter._fiber_send_result,
                            )
                            if outcome["state"] == "unknown":
                                raise InvalidInput("Feishu delivery requires reconciliation")
                    budgets = await feishu_sync.run(
                        feishu_budgets.remaining,
                        config,
                        settings.binding,
                        settings.authorization_id,
                    )
                    processing = "healthy"
                except TimeoutError:
                    processing = "storage_busy"
                instance.status(state, budgets, processing=processing, limits=limits)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass
        except BaseException:
            state, processing = "failed", "failed"
            raise
        finally:
            if intake_task is not None:
                intake_task.cancel()
                await asyncio.gather(intake_task, return_exceptions=True)
            if state != "failed":
                state, processing = "stopping", "stopping"
            if worker is not None and worker.poll() is None:
                worker.terminate()
                try:
                    await asyncio.to_thread(worker.wait, timeout=30)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    await asyncio.to_thread(worker.wait)
            pulse.cancel()
            try:
                await pulse
            except asyncio.CancelledError:
                pass
            try:
                await asyncio.wait_for(runner.stop(), timeout=45)
            finally:
                if state != "failed":
                    state = "stopped"
                instance.status(state, budgets, processing=processing, limits=limits)
    return {"state": "stopped"}
