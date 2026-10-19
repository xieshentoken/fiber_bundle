"""Attach the reviewed receiver to a dedicated Hermes runner before its first start.

No installed source/config edits, platform registry mutation, credential copying or startup.
The caller imports the pinned candidate in a separate process with a dedicated Hermes home.
"""

import importlib
import os
from pathlib import Path
from typing import Any

from . import weixin_inbox as inbox
from . import weixin_receiver as receiver
from .config import Config, InvalidInput, contained
from .execution_config import load_execution_config
from .weixin_receiver_config import ReceiverConfig
from .weixin_worker_config import read_json


def install(
    config: Config, settings: ReceiverConfig, runner: Any, adapter_type: Any, *, home: Path
) -> dict[str, Any]:
    """Use only a Weixin-only, non-multiplex runner and project-local isolated home.

    Repeated installation and adapter construction fail closed. Reconnect of the same
    adapter remains under its existing platform/receiver locks. No builtin fallback.
    """
    settings.validate(config)
    if not settings.enabled:
        return {"state": "disabled"}
    try:
        relative = home.relative_to(config.state_root).as_posix()
    except ValueError:
        raise InvalidInput("dedicated Hermes home must be within project state") from None
    checked_home = contained(config.state_root, relative)
    if not checked_home.is_dir() or checked_home.resolve() != home:
        raise InvalidInput("dedicated Hermes home must be an existing canonical directory")
    if os.environ.get("HERMES_HOME") != str(home):
        raise InvalidInput("dedicated Hermes home must be set before imports")
    constants = importlib.import_module("hermes_constants")
    if Path(constants.get_hermes_home()) != home:
        raise InvalidInput("Hermes context home differs from startup scope")
    gateway = runner.config
    platforms = dict(gateway.platforms)
    if (
        len(platforms) != 1
        or any(getattr(key, "value", None) != "weixin" for key in platforms)
        or gateway.multiplex_profiles
        or runner.adapters
        or getattr(runner, "_running", False)
        or runner._profile_adapters
        or getattr(runner, "_fiber_receiver_installed", False)
        or Path(gateway.sessions_dir) != home / "sessions"
        or getattr(adapter_type, "_fiber_capture_version", None) != 1
    ):
        raise InvalidInput("dedicated unstarted Weixin candidate runner required")
    platform_config = next(iter(platforms.values()))
    extra = platform_config.extra
    if (
        not platform_config.enabled
        or extra.get("account_id") != settings.binding.account_id
        or extra.get("allow_from") != [settings.binding.owner_id]
        or extra.get("dm_policy") != "allowlist"
        or extra.get("group_policy") != "disabled"
    ):
        raise InvalidInput("explicit owner-only Weixin platform configuration required")
    inbox.cursor(config, settings.binding)  # Require prior trusted setup and current authorization.
    policy = None
    if settings.submission_limit:
        path = contained(config.project_root, settings.execution_config)
        read_json(config, path)
        policy = load_execution_config(path)
    used = False

    def create_adapter(platform: Any, candidate_config: Any) -> Any:
        nonlocal used
        if (
            used
            or not candidate_config.enabled
            or getattr(platform, "value", None) != "weixin"
            or candidate_config is not platform_config
            or gateway.platforms != platforms
            or gateway.multiplex_profiles
            or candidate_config.extra.get("account_id") != settings.binding.account_id
            or candidate_config.extra.get("allow_from") != [settings.binding.owner_id]
            or candidate_config.extra.get("dm_policy") != "allowlist"
            or candidate_config.extra.get("group_policy") != "disabled"
            or os.environ.get("HERMES_HOME") != str(home)
            or Path(constants.get_hermes_home()) != home
        ):
            raise InvalidInput("receiver factory cannot expand or replace startup scope")
        used = True  # Failed creation cannot fall through to the legacy Agent poller.
        adapter = adapter_type(candidate_config)
        if Path(adapter._hermes_home) != home:
            raise InvalidInput("adapter home differs from receiver scope")
        adapter.gateway_runner = runner
        receiver.configure(
            adapter,
            config,
            settings.binding,
            quiet_seconds=settings.quiet_seconds,
            intent_routing=settings.intent_routing,
            media_limit=settings.media_limit,
            store_limit=settings.store_limit,
            submission_limit=settings.submission_limit,
            outbound_limit=settings.outbound_limit,
            result_receipts=settings.result_receipts,
            execution_policy=policy,
            capture_policy=settings.capture_policy,
        )
        return adapter

    runner._create_adapter = create_adapter
    runner._fiber_receiver_installed = True
    return {
        "state": "configured",
        "capture_only": not any(
            (
                settings.media_limit,
                settings.store_limit,
                settings.submission_limit,
                settings.outbound_limit,
            )
        ),
    }
