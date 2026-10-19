"""Reuse a hash-pinned Hermes login/adapter without its add-only receiver or Agent path."""

import ast
import asyncio
import hashlib
import importlib
import json
from typing import Any
from urllib.parse import urlsplit

from .. import database, feishu_runtime, weixin_confirmations, weixin_inbox
from ..config import Config, InvalidInput
from ..weixin_events import Binding, message_id
from . import dispatch, store, weixin_poll

PINNED_SHA256 = "f53d73e9a8aa2299616cfa9bc1adb75be80535ad3cff1fc5e63432eba318c77c"


def preflight(adapter: Any) -> None:
    config, channel = adapter._fiber_channel
    with database.locked(config), store.opened(config) as db:
        main = database.connect(config.state_root)
        try:
            from ..vault_relocation.guard import receiving

            with receiving(channel["value"]["vault_id"]):
                dispatch.current(config, main, db, channel["id"], channel["revision"])
        finally:
            main.close()


def query_connect(upstream: Any) -> Any:
    """Keep pinned platform locks/connect cleanup; substitute only the scope check import."""
    from pathlib import Path

    source = Path(upstream.__file__).read_bytes()
    if hashlib.sha256(source).hexdigest() != PINNED_SHA256:
        raise InvalidInput("channel_weixin_runtime_changed")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WeixinAdapter")
    method = next(
        n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "connect"
    )
    imports = [
        n
        for n in ast.walk(method)
        if isinstance(n, ast.ImportFrom) and n.module == "fiber_bundle.weixin_receiver"
    ]
    if len(imports) != 1 or [a.name for a in imports[0].names] != ["preflight"]:
        raise InvalidInput("channel_weixin_preflight_contract_changed")
    imports[0].module = "fiber_bundle.channels.weixin"
    namespace = dict(vars(upstream))
    exec(
        compile(ast.Module(body=[method], type_ignores=[]), "fiber-weixin-connect", "exec"),
        namespace,
    )
    return namespace["connect"]


def upstream() -> Any:
    value = importlib.import_module("gateway.platforms.weixin")
    query_connect(value)  # Reject drift before login or any API call.
    return value


async def login(config: Config, channel: dict[str, Any]) -> dict[str, Any]:
    qrcode = importlib.import_module("qrcode")
    PyPNGImage = importlib.import_module("qrcode.image.pure").PyPNGImage

    module = upstream()
    home = store.home(config, channel["id"])
    original = qrcode.QRCode.print_ascii

    def show(qr: Any, *_args: Any, **_kwargs: Any) -> None:
        temporary = home / "login-qr.tmp.png"
        qr.make_image(image_factory=PyPNGImage).save(str(temporary))
        temporary.chmod(0o600)
        temporary.replace(home / "login-qr.png")
        feishu_runtime.write(home / "login-status.json", {"state": "waiting_for_scan"})

    qrcode.QRCode.print_ascii = show
    try:
        credentials = await module.qr_login(str(home), timeout_seconds=480)
    finally:
        qrcode.QRCode.print_ascii = original
    if not credentials or not credentials.get("user_id"):
        raise InvalidInput("channel_weixin_login_not_completed")
    candidate = {
        "owner_id": credentials["user_id"],
        "account_id": credentials["account_id"],
        "base_url": credentials["base_url"],
    }
    import re

    if any(
        not isinstance(candidate[k], str)
        or not re.fullmatch(r"[A-Za-z0-9_@.:-]{1,400}", candidate[k])
        or ".." in candidate[k]
        for k in ("owner_id", "account_id")
    ):
        raise InvalidInput("channel_weixin_login_identity_invalid")
    route(candidate["base_url"])
    feishu_runtime.write(home / "weixin-login.json", candidate)
    (home / "login-qr.png").unlink(missing_ok=True)
    return candidate


def route(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".weixin.qq.com")
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise InvalidInput("channel_weixin_api_route_invalid")


def adapter(config: Config, channel: dict[str, Any]) -> Any:
    module = upstream()
    home = store.home(config, channel["id"])
    from ..query_routing_files import read

    login = json.loads(read(home / "weixin-login.json", 4096))
    if login["owner_id"] != channel["owner"]:
        raise InvalidInput("channel_weixin_owner_changed")
    route(login["base_url"])
    pc_type = importlib.import_module("gateway.config").PlatformConfig
    connect = query_connect(module)

    base = module.WeixinAdapter

    class Adapter(base):  # type: ignore[misc,valid-type]
        _running: bool
        _fiber_error: str | None
        _fiber_exception: Exception | None
        _fiber_poll_state: str

        async def connect(self, *, is_reconnect: bool = False) -> bool:
            return bool(await connect(self, is_reconnect=is_reconnect))

        async def _poll_loop(self) -> None:
            cursor = ""
            path = home / "channel-cursor.json"
            if path.exists():
                cursor = json.loads(read(path, 65536))["cursor"]
            try:
                while self._running:
                    response = await weixin_poll.receive(
                        self, module, cursor, lambda: preflight(self)
                    )
                    messages = response.get("msgs", [])
                    if not isinstance(messages, list) or len(messages) > 100:
                        raise InvalidInput("channel_weixin_response_limit")
                    archive = []
                    for msg in messages:
                        if (
                            msg.get("from_user_id") != channel["owner"]
                            or msg.get("to_user_id") != self._account_id
                            or msg.get("room_id")
                            or msg.get("chat_room_id")
                        ):
                            continue
                        items = msg.get("item_list", [])
                        if (
                            len(items) == 1
                            and items[0].get("type") == 1
                            and not items[0].get("ref_msg")
                        ):
                            is_query = dispatch.capture(
                                config,
                                channel["id"],
                                channel["revision"],
                                message_id(msg["message_id"]),
                                channel["owner"],
                                items[0].get("text_item", {}).get("text", ""),
                                msg.get("context_token", ""),
                            )
                            if is_query:
                                continue
                        from .query import question

                        if channel["value"]["add"] and not (
                            len(items) == 1
                            and items[0].get("type") == 1
                            and question(items[0].get("text_item", {}).get("text", "")) is not None
                        ):
                            archive.append(msg)
                    if channel["value"]["add"]:
                        binding = Binding(
                            self._account_id,
                            channel["owner"],
                            channel["value"]["client_id"],
                            channel["value"]["vault_id"],
                        )
                        expected = weixin_inbox.cursor(config, binding)
                        weixin_inbox.receive(
                            config,
                            binding,
                            expected,
                            {
                                "msgs": archive,
                                "get_updates_buf": response.get("get_updates_buf", expected),
                            },
                            guard=lambda: dispatch.archival_guard(
                                config, channel["id"], channel["revision"]
                            ),
                        )
                        weixin_confirmations.tick(config, binding)
                    cursor = response.get("get_updates_buf") or cursor
                    if not isinstance(cursor, str) or len(cursor) > 60000:
                        raise InvalidInput("channel_weixin_cursor_invalid")
                    feishu_runtime.write(path, {"cursor": cursor})
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._fiber_exception = error
                self._fiber_error = "channel_weixin_poll_failed"
                self._running = False

    value = Adapter(
        pc_type(
            enabled=True,
            gateway_restart_notification=False,
            extra={
                "account_id": login["account_id"],
                "base_url": login["base_url"],
                "dm_policy": "disabled",
                "group_policy": "disabled",
            },
        )
    )
    value._fiber_channel = (config, channel)
    value._fiber_error = None
    value._fiber_exception = None
    value._fiber_poll_state = "connected"
    return value


async def send(
    adapter: Any, owner: str, body: str | dict[str, Any], context: str, guard: Any
) -> bool:
    from .weixin_output import send as output

    module = upstream()
    route(adapter._base_url)
    return await output(adapter, module, owner, body, context, guard, preflight)
