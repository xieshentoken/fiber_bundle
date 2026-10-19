"""One challenge-bound identity candidate; no Fiber intake, grants, models or sends."""

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

from .config import Config, InvalidInput
from .feishu_gateway import Settings, adapter_class
from .inputs import text


def candidate(settings: Settings, payload: dict[str, Any], code: str) -> dict[str, str] | None:
    if payload.get("header", {}).get("app_id") != settings.app_id:
        return None
    event = payload.get("event", {})
    sender, message = event.get("sender", {}), event.get("message", {})
    if sender.get("sender_type") != "user" or message.get("chat_type") != "p2p":
        return None
    if message.get("message_type") != "text":
        return None
    try:
        content = json.loads(message.get("content", ""))
    except (TypeError, ValueError):
        return None
    if content != {"text": code}:
        return None
    owner = text(sender.get("sender_id", {}).get("open_id"), limit=400)
    if not owner.startswith("ou_"):
        raise InvalidInput("pairing requires app-scoped owner identity")
    return {
        "app_id": settings.app_id,
        "owner_id": owner,
        "message_id": text(message.get("message_id"), limit=400),
        "chat_id": text(message.get("chat_id"), limit=400),
    }


async def run(
    config: Config,
    settings: Settings,
    home: Path,
    secret: str,
    code: str,
    upstream: Any,
    pc_type: Any,
) -> dict[str, Any]:
    text(code, limit=200)
    if len(code) < 20:
        raise InvalidInput("explicit long pairing challenge required")
    if (home / "pairing-candidate.json").exists():
        raise InvalidInput("existing pairing candidate needs local review")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    base = adapter_class(upstream.FeishuAdapter, upstream.lark, config, settings)

    class PairingAdapter(base):  # type: ignore[misc,valid-type]
        def _on_message_event(self, data: Any) -> None:
            try:
                value = candidate(settings, json.loads(upstream.lark.JSON.marshal(data)), code)
                if value is not None:
                    with self._fiber_callback_lock:
                        path = home / "pairing-candidate.json"
                        if not path.exists():
                            with path.open("x") as stream:
                                json.dump(value, stream)
                                stream.flush()
                                os.fsync(stream.fileno())
                            path.chmod(0o600)
                    loop.call_soon_threadsafe(stop.set)
            except Exception as exc:
                self._fiber_error = type(exc).__name__
                raise

    pc = pc_type(
        enabled=True,
        gateway_restart_notification=False,
        extra={
            "app_id": settings.app_id,
            "app_secret": secret,
            "domain": "feishu",
            "connection_mode": "websocket",
            "dm_policy": "disabled",
            "group_policy": "disabled",
        },
    )
    adapter = PairingAdapter(pc)
    status_path = home / "pairing-status.json"

    def status(state: str) -> None:
        status_path.write_text(
            json.dumps(
                {
                    "state": state,
                    "pid": os.getpid(),
                    "intake_limit": 0,
                    "model_limit": 0,
                    "outbound_limit": 0,
                }
            )
            + "\n"
        )
        status_path.chmod(0o600)

    status("starting")
    try:
        if not await asyncio.wait_for(adapter.connect(), timeout=45):
            raise InvalidInput("Feishu pairing connection failed")
        status("waiting_for_challenge")
        while not stop.is_set():
            if adapter._fiber_error or (adapter._ws_future and adapter._ws_future.done()):
                raise InvalidInput("Feishu pairing receiver stopped")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
    except BaseException:
        status("failed")
        raise
    finally:
        await adapter.disconnect()
    found = (home / "pairing-candidate.json").exists()
    state = "candidate_received_needs_local_confirmation" if found else "stopped"
    status(state)
    return {"state": state, "intake_limit": 0}
