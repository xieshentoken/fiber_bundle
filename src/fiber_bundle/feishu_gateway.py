"""Install an archival adapter on one isolated, unstarted Feishu-only Hermes runner."""

import importlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from . import feishu_receiver
from .config import Config, InvalidInput, contained
from .weixin_events import Binding
from .weixin_inbox import cursor


@dataclass(frozen=True)
class Settings:
    app_id: str
    owner_id: str
    client_id: str
    vault_id: str
    home: str
    secret_file: str
    enabled: bool = False
    quiet_seconds: float = 3
    download_limit: int = 0
    store_limit: int = 0
    version: int = 1
    authorization_id: str | None = None
    expert_enabled: bool = False
    expert_limit: int = 0
    query_enabled: bool = False
    model_limit: int = 0
    outbound_limit: int = 0
    execution_file: str | None = None

    @property
    def binding(self) -> Binding:
        return Binding("feishu:" + self.app_id, self.owner_id, self.client_id, self.vault_id)

    def validate(self, config: Config) -> Path:
        from .weixin_batches import seconds

        config.validate()
        self.binding.validate()
        if type(self.enabled) is not bool or type(self.version) is not int or self.version != 1:
            raise InvalidInput("invalid Feishu startup version or switch")
        if not self.app_id.startswith("cli_") or not self.owner_id.startswith("ou_"):
            raise InvalidInput("explicit Feishu app ID and app-scoped owner open_id required")
        seconds(self.quiet_seconds)
        for limit in (
            self.download_limit,
            self.store_limit,
            self.model_limit,
            self.outbound_limit,
            self.expert_limit,
        ):
            if type(limit) is not int or not 0 <= limit <= 100:
                raise InvalidInput("Feishu limits must be integers from zero to 100")
        if self.authorization_id is not None:
            try:
                if str(UUID(self.authorization_id)) != self.authorization_id:
                    raise ValueError("noncanonical authorization")
            except (ValueError, TypeError, AttributeError):
                raise InvalidInput("canonical Feishu authorization UUID required") from None
        elif any(
            (
                self.download_limit,
                self.store_limit,
                self.model_limit,
                self.outbound_limit,
                self.expert_limit,
            )
        ):
            raise InvalidInput("nonzero limits require an explicit authorization_id")
        if type(self.expert_enabled) is not bool or (self.expert_limit and not self.expert_enabled):
            raise InvalidInput("explicit Feishu expert switch required")
        if type(self.query_enabled) is not bool or (
            (self.model_limit or self.outbound_limit) and not self.query_enabled
        ):
            raise InvalidInput("explicit Feishu query switch required")
        if self.query_enabled:
            if (
                not isinstance(self.execution_file, str)
                or not Path(self.execution_file).is_absolute()
            ):
                raise InvalidInput("explicit Feishu query execution file required")
            from .execution_config import load_execution_config

            policy = load_execution_config(Path(self.execution_file))
            if policy.max_calls != 1 or policy.max_attempts != 1:
                raise InvalidInput("Feishu query policy requires one call and no retries")
        if not isinstance(self.secret_file, str) or not Path(self.secret_file).is_absolute():
            raise InvalidInput("explicit absolute Feishu secret file required")
        if not self.home.startswith("configs/hermes-feishu-"):
            raise InvalidInput("dedicated Feishu configuration home required")
        return contained(config.state_root, self.home)


def adapter_class(base: Any, sdk: Any, config: Config, settings: Settings) -> Any:
    """Subclass public pinned code locally; never modify the existing Hermes installation."""

    class ArchivalFeishu(base):  # type: ignore[misc]
        _fiber_capture_version = 1

        def __init__(self, platform_config: Any) -> None:
            super().__init__(platform_config)
            self._fiber_callback_lock = threading.Lock()
            self._fiber_error: str | None = None

        def _build_event_handler(self) -> Any:
            return (
                sdk.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(self._on_message_event)
                .build()
            )

        def _build_lark_client(self, domain: Any) -> Any:
            return (
                sdk.Client.builder()
                .app_id(self._app_id)
                .app_secret(self._app_secret)
                .domain(domain)
                .timeout(30)
                .log_level(sdk.LogLevel.ERROR)
                .build()
            )

        def _on_message_event(self, data: Any) -> None:
            try:
                payload = json.loads(sdk.JSON.marshal(data))
                with self._fiber_callback_lock:
                    from . import database

                    with database.lock_wait(1):
                        feishu_receiver.capture(
                            config,
                            settings.binding,
                            payload,
                            commands=settings.query_enabled,
                            expert_authorization=settings.authorization_id
                            if settings.expert_enabled
                            else None,
                            expert_limit=settings.expert_limit,
                            guard=lambda: _channel_guard(config, settings),
                        )
            except Exception as exc:
                self._fiber_error = type(exc).__name__
                # The private launcher suppresses SDK logs, but capture must fail visibly.
                raise

        async def handle_message(self, *_args: Any, **_kwargs: Any) -> None:
            raise InvalidInput("legacy Feishu Agent dispatch forbidden")

        def _fiber_send_result(self, target: str, body: str, guard: Any) -> dict[str, Any]:
            if (
                not settings.query_enabled
                or not settings.outbound_limit
                or target != settings.owner_id
            ):
                raise InvalidInput("controlled Feishu delivery unavailable")
            from .feishu_delivery import sdk_send

            def checked() -> None:
                _channel_guard(config, settings)
                guard()

            return sdk_send(sdk, self._client, target, body, checked)

        async def send(self, *_args: Any, **_kwargs: Any) -> Any:
            raise InvalidInput("Feishu outbound disabled for archival gateway")

        def _fiber_send_receipt(self, target: str, body: str, guard: Any) -> dict[str, Any]:
            if target != settings.owner_id:
                raise InvalidInput("controlled Feishu receipt unavailable")
            from .feishu_delivery import sdk_send

            def checked() -> None:
                _channel_guard(config, settings)
                guard()

            return sdk_send(sdk, self._client, target, body, checked)

    return ArchivalFeishu


def install(config: Config, settings: Settings, runner: Any, adapter_type: Any) -> dict[str, Any]:
    home = settings.validate(config)
    if not settings.enabled:
        return {"state": "disabled"}
    _channel_guard(config, settings)
    if not home.is_dir() or home.resolve() != home or os.environ.get("HERMES_HOME") != str(home):
        raise InvalidInput("canonical dedicated Hermes home required before imports")
    constants = importlib.import_module("hermes_constants")
    if Path(constants.get_hermes_home()) != home:
        raise InvalidInput("Hermes imported with a different home")
    gateway = runner.config
    platforms = dict(gateway.platforms)
    if (
        len(platforms) != 1
        or any(getattr(key, "value", None) != "feishu" for key in platforms)
        or gateway.multiplex_profiles
        or runner.adapters
        or runner._profile_adapters
        or getattr(runner, "_running", False)
        or getattr(runner, "_fiber_receiver_installed", False)
        or Path(gateway.sessions_dir) != home / "sessions"
        or getattr(adapter_type, "_fiber_capture_version", None) != 1
    ):
        raise InvalidInput("unstarted Feishu-only archival runner required")
    platform_config = next(iter(platforms.values()))
    extra = platform_config.extra
    if (
        not platform_config.enabled
        or extra.get("app_id") != settings.app_id
        or extra.get("connection_mode") != "websocket"
        or extra.get("allow_from") != [settings.owner_id]
        or extra.get("dm_policy") != "allowlist"
        or extra.get("group_policy") != "disabled"
    ):
        raise InvalidInput("owner-only Feishu websocket configuration required")
    cursor(config, settings.binding)
    original_extra = dict(extra)
    used = False

    def create_adapter(platform: Any, candidate_config: Any) -> Any:
        nonlocal used
        if (
            used
            or candidate_config is not platform_config
            or not candidate_config.enabled
            or getattr(platform, "value", None) != "feishu"
            or candidate_config.extra != original_extra
            or gateway.platforms != platforms
            or gateway.multiplex_profiles
            or os.environ.get("HERMES_HOME") != str(home)
            or Path(constants.get_hermes_home()) != home
        ):
            raise InvalidInput("Feishu startup scope changed or already constructed")
        used = True
        adapter = adapter_type(candidate_config)
        adapter.gateway_runner = runner
        return adapter

    runner._create_adapter = create_adapter
    runner._fiber_receiver_installed = True
    return {
        "state": "configured",
        "platform": "feishu",
        "outbound_limit": settings.outbound_limit,
    }


def _channel_guard(config: Config, settings: Settings) -> None:
    from .channels.legacy_control import guard

    guard(config, settings.home)
