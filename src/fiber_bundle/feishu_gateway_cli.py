"""Explicit isolated Hermes Feishu archival startup; no Agent/model/publication worker."""

import argparse
import asyncio
import contextlib
import importlib
import json
import logging
import os
import re
import signal
import sys
from pathlib import Path
from typing import Any

from . import feishu_gateway, feishu_gateway_loop, feishu_runtime, feishu_sync
from .config import Config, InvalidInput, contained, load_config
from .execution_config import read_secret
from .objects import digest
from .weixin_worker_config import read_json


class SilentOutput:
    encoding = "utf-8"

    def fileno(self) -> int:
        # Hermes falls back to its private fault log for unsupported file descriptors.
        # Never expose the original stdout/stderr descriptor to third-party code.
        raise OSError("silent output has no file descriptor")

    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def isolate(check_only: bool) -> None:
    original = Path.home() / ".hermes"

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "open" and isinstance(args[0], (str, bytes)):
            path = Path(os.fsdecode(args[0])).absolute()
            if path.is_relative_to(original):
                raise InvalidInput("original Hermes home access forbidden")
        if check_only and event in {"socket.connect", "socket.getaddrinfo"}:
            raise InvalidInput("network forbidden in Feishu runtime preflight")

    sys.addaudithook(audit)


def verify_component(config: Config, manifest: dict[str, Any]) -> None:
    if "component" in manifest:
        from .runtime_components import verify

        selection = manifest["component"]
        root = contained(config.project_root, selection["root"])
        verify(root, root / "component.json", selection["sha256"])


def bootstrap(config: Config, manifest: dict[str, Any], home: Path) -> None:
    """Only project-local pinned runtime files; bind the private home before Hermes imports."""
    verify_component(config, manifest)
    if set(manifest) - {"component"} != {
        "source",
        "dependencies",
        "sha256",
        "scanner",
        "scanner_sha256",
    }:
        raise InvalidInput("invalid Feishu runtime manifest")
    source = contained(config.project_root, manifest["source"])
    if (
        not isinstance(manifest["dependencies"], list)
        or not 1 <= len(manifest["dependencies"]) <= 3
    ):
        raise InvalidInput("explicit Feishu dependency directories required")
    dependencies = [contained(config.project_root, path) for path in manifest["dependencies"]]
    if (
        not source.is_dir()
        or not all(path.is_dir() for path in dependencies)
        or not isinstance(manifest["sha256"], dict)
    ):
        raise InvalidInput("Feishu runtime directories unavailable")
    for relative, expected in manifest["sha256"].items():
        path = contained(source, relative)
        if digest(path.read_bytes()) != expected:
            raise InvalidInput("pinned Hermes runtime changed")
    scanner = contained(config.project_root, manifest["scanner"])
    if not scanner.is_file() or digest(scanner.read_bytes()) != manifest["scanner_sha256"]:
        raise InvalidInput("pinned security scanner unavailable or changed")
    if "hermes_constants" in sys.modules:
        raise InvalidInput("Hermes must be imported after setting its independent home")
    for key in list(os.environ):
        if key.startswith(("FEISHU_", "HERMES_")) or key.endswith(("_API_KEY", "_API_TOKEN")):
            os.environ.pop(key)
    os.environ["HERMES_HOME"] = str(home)
    os.environ["TIRITH_BIN"] = str(scanner)  # No background download from Hermes's constructor.
    # Preserve Hermes's default machine-wide app-ID lock; never redirect it into this home.
    sys.path[:0] = [str(source), *(str(path) for path in dependencies)]
    logging.disable(logging.CRITICAL)


def load_secret(settings: feishu_gateway.Settings) -> str:
    path = Path(settings.secret_file)
    if path.suffix.lower() == ".rtf":
        return read_secret(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise InvalidInput("invalid Feishu credential file")
    try:
        raw = path.read_text()
        if path.suffix.lower() == ".json":
            data = json.loads(raw)
        else:
            app = re.fullmatch(
                r"(?:app[_ -]?id|id)\s*[:=：]\s*(cli_[A-Za-z0-9]+)",
                raw.splitlines()[0].strip(),
                re.I,
            )
            secret = re.fullmatch(
                r"(?:app[_ -]?secret|secret)\s*[:=：]\s*([A-Za-z0-9_-]{20,4096})",
                raw.splitlines()[1].strip(),
                re.I,
            )
            if len(raw.splitlines()) != 2 or not app or not secret:
                raise InvalidInput("invalid Feishu credential text")
            data = {"app_id": app[1], "app_secret": secret[1]}
    except (OSError, ValueError, IndexError):
        raise InvalidInput("invalid Feishu credential file") from None
    if not isinstance(data, dict) or set(data) != {"app_id", "app_secret"}:
        raise InvalidInput("Feishu credentials require exactly app_id and app_secret")
    if data["app_id"] != settings.app_id:
        raise InvalidInput("Feishu credential application mismatch")
    value = data["app_secret"]
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,4096}", value):
        raise InvalidInput("invalid Feishu secret")
    return value


async def run(config: Config, settings: feishu_gateway.Settings, home: Path) -> dict[str, Any]:
    gateway_config = importlib.import_module("gateway.config")
    Platform, PlatformConfig = gateway_config.Platform, gateway_config.PlatformConfig
    GatewayConfig = gateway_config.GatewayConfig
    GatewayRunner = importlib.import_module("gateway.run").GatewayRunner
    upstream = importlib.import_module("plugins.platforms.feishu.adapter")

    if not settings.enabled:
        return {"state": "disabled"}
    secret = load_secret(settings)
    pc = PlatformConfig(
        enabled=True,
        gateway_restart_notification=False,
        extra={
            "app_id": settings.app_id,
            "app_secret": secret,
            "domain": "feishu",
            "connection_mode": "websocket",
            "allow_from": [settings.owner_id],
            "dm_policy": "allowlist",
            "group_policy": "disabled",
        },
    )
    runner = GatewayRunner(
        GatewayConfig(
            platforms={Platform.FEISHU: pc}, sessions_dir=home / "sessions", stt_enabled=False
        )
    )
    candidate = feishu_gateway.adapter_class(
        upstream.FeishuAdapter, upstream.lark, config, settings
    )
    await feishu_sync.run(feishu_gateway.install, config, settings, runner, candidate)

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise InvalidInput("legacy Agent dispatch forbidden")

    runner._handle_message = forbidden
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    return await feishu_gateway_loop.run(config, settings, home, runner, Platform.FEISHU, stop)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--check-runtime", action="store_true")
    parser.add_argument("--pairing-code")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        settings = feishu_gateway.Settings(**read_json(config, args.settings))
        home = settings.validate(config)
        if not home.is_dir() or home.resolve() != home:
            raise InvalidInput("prepared independent Hermes home required")
        if args.status:
            print(json.dumps(feishu_runtime.inspect(home)))
            return 0
        isolate(args.check_runtime)
        with contextlib.redirect_stdout(SilentOutput()), contextlib.redirect_stderr(SilentOutput()):
            bootstrap(config, read_json(config, args.runtime_manifest), home)
            if args.check_runtime:
                upstream = importlib.import_module("plugins.platforms.feishu.adapter")
                importlib.import_module("gateway.run")
                if not upstream.FEISHU_AVAILABLE:
                    raise InvalidInput("Feishu SDK unavailable")
                result = {"state": "runtime_checked", "network": "not_started"}
            elif args.pairing_code:
                from . import feishu_pairing

                upstream = importlib.import_module("plugins.platforms.feishu.adapter")
                pc_type = importlib.import_module("gateway.config").PlatformConfig
                result = asyncio.run(
                    feishu_pairing.run(
                        config,
                        settings,
                        home,
                        load_secret(settings),
                        args.pairing_code,
                        upstream,
                        pc_type,
                    )
                )
            else:
                result = asyncio.run(run(config, settings, home))
        print(json.dumps(result))
        return 0
    except Exception as exc:
        # Never serialize exception messages, SDK responses, source text or credentials.
        print(
            json.dumps(
                {
                    "state": "failed",
                    "error_type": type(exc).__name__,
                    **(
                        {"missing_module": exc.name} if isinstance(exc, ModuleNotFoundError) else {}
                    ),
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
